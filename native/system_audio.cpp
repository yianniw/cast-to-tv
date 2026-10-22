#include <audioclient.h>
#include <mmdeviceapi.h>
#include <wrl/client.h>
#include <array>
#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>
#include "opus.h"
#include "system_audio.h"

using Microsoft::WRL::ComPtr;
using openscreen::Clock;
using namespace std::chrono_literals;
namespace {
void Check(HRESULT status, const char* action) {
  if (FAILED(status)) {
    if (status == AUDCLNT_E_DEVICE_INVALIDATED)
      throw std::runtime_error("Windows playback device was disconnected or disabled. Reconnect it, or turn off Include system audio and restart mirroring.");
    std::stringstream error; error << action << " (HRESULT 0x" << std::hex << uint32_t(status) << ')';
    throw std::runtime_error(error.str());
  }
}
Clock::time_point CaptureTime(UINT64 qpc_position) {
  LARGE_INTEGER counter{}, frequency{};
  QueryPerformanceCounter(&counter); QueryPerformanceFrequency(&frequency);
  double current_us = double(counter.QuadPart) * 1000000.0 / frequency.QuadPart;
  double captured_us = double(qpc_position) / 10.0;
  return Clock::now() - Clock::duration(static_cast<int64_t>(current_us - captured_us));
}
}  // namespace
SystemAudio::SystemAudio() {
  stop_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (!stop_) throw std::runtime_error("Cannot create audio stop event");
}
SystemAudio::~SystemAudio() {
  SetEvent(stop_);
  if (worker_.joinable()) worker_.join();
  CloseHandle(stop_);
}
void SystemAudio::Start() { worker_ = std::thread([this] { Capture(); }); }
bool SystemAudio::Next(AudioPacket& packet) {
  std::lock_guard lock(mutex_);
  if (!error_.empty()) throw std::runtime_error(error_);
  if (packets_.empty()) return false;
  packet = std::move(packets_.front()); packets_.pop_front(); return true;
}
void SystemAudio::Capture() {
  HANDLE wake = nullptr;
  OpusEncoder* encoder = nullptr;
  bool com = false;
  ComPtr<IAudioClient> client;
  try {
    Check(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "Initialize audio COM"); com = true;
    ComPtr<IMMDeviceEnumerator> devices;
    Check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                           IID_PPV_ARGS(&devices)), "Enumerate playback devices");
    ComPtr<IMMDevice> device;
    Check(devices->GetDefaultAudioEndpoint(eRender, eConsole, &device),
          "No default Windows playback device; enable speakers/headphones or disable system audio");
    Check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                           reinterpret_cast<void**>(client.GetAddressOf())), "Open loopback audio");
    WAVEFORMATEX format{};
    format.wFormatTag = WAVE_FORMAT_IEEE_FLOAT; format.nChannels = 2; format.nSamplesPerSec = 48000;
    format.wBitsPerSample = 32; format.nBlockAlign = 8; format.nAvgBytesPerSec = 384000;
    DWORD flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
                  AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
    Check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 2000000, 0, &format, nullptr),
          "Initialize stereo 48 kHz loopback");
    wake = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!wake) throw std::runtime_error("Cannot create audio capture event");
    Check(client->SetEventHandle(wake), "Set audio capture event");
    ComPtr<IAudioCaptureClient> capture;
    Check(client->GetService(IID_PPV_ARGS(&capture)), "Open loopback capture client");
    int error = 0;
    encoder = opus_encoder_create(48000, 2, OPUS_APPLICATION_RESTRICTED_LOWDELAY, &error);
    if (!encoder || error != OPUS_OK) throw std::runtime_error("Cannot create Opus encoder");
    if (opus_encoder_ctl(encoder, OPUS_SET_BITRATE(128000)) != OPUS_OK ||
        opus_encoder_ctl(encoder, OPUS_SET_COMPLEXITY(5)) != OPUS_OK)
      throw std::runtime_error("Cannot configure Opus encoder");
    Check(client->Start(), "Start system audio capture");
    std::vector<float> pending; pending.reserve(1920);
    Clock::time_point pending_time{}, last_reference = Clock::time_point::min();
    auto origin = Clock::now(); auto last_data = origin;
    uint64_t last_samples = 0;
    auto encode = [&](const float* pcm, Clock::time_point reference) {
      if (reference <= last_reference) return;
      auto elapsed = (reference - origin).count();
      uint64_t samples = elapsed > 0 ? uint64_t(elapsed) * 48000 / 1000000 : 0;
      if (last_reference != Clock::time_point::min()) samples = std::max(samples, last_samples + 960);
      AudioPacket packet; packet.reference = reference; packet.samples = samples; packet.data.resize(4000);
      int count = opus_encode_float(encoder, pcm, 960, packet.data.data(), int(packet.data.size()));
      if (count < 0) throw std::runtime_error(std::string("Opus audio encoding failed: ") + opus_strerror(count));
      packet.data.resize(count); last_reference = reference; last_samples = samples;
      std::lock_guard lock(mutex_);
      if (packets_.size() == 5) { replaced_ += packets_.size(); packets_.clear(); }
      packets_.push_back(std::move(packet));
    };
    HANDLE handles[]{stop_, wake};
    for (;;) {
      auto waited = WaitForMultipleObjects(2, handles, FALSE, 20);
      if (waited == WAIT_OBJECT_0) break;
      if (waited == WAIT_FAILED) throw std::runtime_error("Audio capture event wait failed");
      UINT32 available = 0;
      Check(capture->GetNextPacketSize(&available), "Check loopback audio packet");
      while (available) {
        BYTE* data = nullptr; UINT32 frames = 0; DWORD status = 0; UINT64 device_position = 0, qpc = 0;
        Check(capture->GetBuffer(&data, &frames, &status, &device_position, &qpc), "Read system audio");
        try {
          auto reference = status & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR ? Clock::now() - 10ms : CaptureTime(qpc);
          if (status & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) {
            ++discontinuities_; pending.clear();
          }
          for (UINT32 i = 0; i < frames; ++i) {
            if (pending.empty()) pending_time = reference + Clock::duration(uint64_t(i) * 1000000 / 48000);
            const float* samples = reinterpret_cast<const float*>(data);
            for (int channel = 0; channel < 2; ++channel) {
              float value = status & AUDCLNT_BUFFERFLAGS_SILENT ? 0.0f : samples[i * 2 + channel];
              pending.push_back(std::isfinite(value) ? std::clamp(value, -1.0f, 1.0f) : 0.0f);
            }
            if (pending.size() == 1920) { encode(pending.data(), pending_time); pending.clear(); }
          }
        } catch (...) { capture->ReleaseBuffer(frames); throw; }
        Check(capture->ReleaseBuffer(frames), "Release audio packet");
        last_data = Clock::now();
        Check(capture->GetNextPacketSize(&available), "Check next loopback audio packet");
      }
      // Idle Windows render engines may supply no packets. Keep the negotiated
      // track alive with silence without accumulating a backlog of old samples.
      auto now = Clock::now();
      if (now - last_data > 100ms && (last_reference == Clock::time_point::min() || now - last_reference >= 20ms)) {
        pending.clear(); std::array<float, 1920> silence{}; encode(silence.data(), now - 20ms);
      }
    }
  } catch (const std::exception& error) {
    std::lock_guard lock(mutex_); error_ = error.what();
  }
  if (client) client->Stop();
  client.Reset();
  if (encoder) opus_encoder_destroy(encoder);
  if (wake) CloseHandle(wake);
  if (com) CoUninitialize();
}
