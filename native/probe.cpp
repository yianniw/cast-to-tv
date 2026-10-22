// Native helper entry point: configure media, control, and negotiated senders.
#include <windows.h>
#ifdef SendMessage
#undef SendMessage
#endif
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>
#include "control/input.h"
#include "control/protocol.h"
#include "sender/probe.h"
#include "cast/streaming/public/environment.h"

using namespace openscreen;
using namespace openscreen::cast;
using namespace std::chrono_literals;

using namespace cast_to_tv;

int main(int argc, char** argv) {
  try {
    if (argc < 6)
      throw std::runtime_error("Usage: cast_streaming_probe HOST SOURCE_ID DESTINATION_ID INPUT PLAYOUT_MS [--desktop-config] [--audio-clip FILE]");
    bool desktop_input = false, system_audio_input = false; const char* audio_path = nullptr;
    for (int i = 6; i < argc; ++i) {
      if (std::string(argv[i]) == "--desktop-config") desktop_input = true;
      else if (std::string(argv[i]) == "--system-audio") system_audio_input = true;
      else if (std::string(argv[i]) == "--audio-clip" && i + 1 < argc) audio_path = argv[++i];
      else throw std::runtime_error("Unknown helper option");
    }
    auto address = IPAddress::Parse(argv[1]);
    if (address.is_error() || !address.value().IsV4()) throw std::runtime_error("An IPv4 receiver address is required");
    int delay = std::stoi(argv[5]);
    if (delay < 100 || delay > 1000) throw std::runtime_error("Playout must be 100..1000 milliseconds");
    Clip clip(argv[4], desktop_input);
    std::unique_ptr<OpusClip> audio;
    std::unique_ptr<SystemAudio> system_audio;
    if (audio_path && system_audio_input) throw std::runtime_error("Choose a synthetic clip or system audio");
    if (audio_path) audio = std::make_unique<OpusClip>(audio_path);
    if (system_audio_input) system_audio = std::make_unique<SystemAudio>();
    WindowsTaskRunner runner; StdioPort port;
    Environment environment(&Clock::now, runner);
    Probe probe(runner, clip, audio.get(), system_audio.get());
    SenderSession::Configuration configuration{address.value(), probe, &environment, &port, argv[2], argv[3]};
    configuration.max_in_flight_media_duration = std::chrono::milliseconds(150);
    SenderSession session(std::move(configuration));
    struct ReleaseBeforeSession { Probe& probe; ~ReleaseBeforeSession() { probe.ReleaseSender(); } } release{probe};
    VideoCaptureConfig video; video.resolutions.push_back({clip.width, clip.height});
    video.max_frame_rate = {clip.rate, clip.scale}; video.max_bit_rate = 8000000;
    video.target_playout_delay = std::chrono::milliseconds(delay);
    ControlInput input(runner, port, probe); runner.PostTask([&input] { input.Poll(); });
    std::vector<AudioCaptureConfig> audio_configs;
    if (audio || system_audio) {
      AudioCaptureConfig config; config.bit_rate = 128000; config.target_playout_delay = video.target_playout_delay;
      audio_configs.push_back(config);
    }
    runner.PostTask([&] { auto error = session.Negotiate(audio_configs, {video});
      if (error.code() != Error::Code::kNone) { std::stringstream message; message << error; probe.Fail(message.str()); } });
    runner.Run(); probe.ReleaseSender(); return probe.result;
  } catch (const std::exception& error) {
    Json::Value event; event["event"] = "error"; event["message"] = error.what(); Emit(event); return 1;
  }
}
