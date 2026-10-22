#include "capture/command.h"
#include <iostream>
#include <stdexcept>
#include <string>
#include "desktop_source.h"

namespace {
std::runtime_error WindowsError(const char* operation) {
  return std::runtime_error(std::string(operation) + ": Windows " + std::to_string(GetLastError()));
}
std::wstring Wide(const std::string& value) {
  int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), int(value.size()), nullptr, 0);
  if (!length) throw WindowsError("UTF-8 conversion");
  std::wstring result(length, L'\0');
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), int(value.size()), result.data(), length);
  return result;
}
// Quote each argv element using Windows CRT rules; no shell is involved.
std::wstring Quote(const std::wstring& text) {
  std::wstring result = L"\""; size_t slashes = 0;
  for (wchar_t c : text) {
    if (c == L'\\') { ++slashes; continue; }
    result.append(c == L'\"' ? slashes * 2 + 1 : slashes, L'\\');
    result += c; slashes = 0;
  }
  result.append(slashes * 2, L'\\'); result += L'\"'; return result;
}
uint64_t Little(const uint8_t* data, size_t count) {
  uint64_t result = 0;
  for (size_t i = 0; i < count; ++i) result |= uint64_t(data[i]) << (8 * i);
  return result;
}
}  // namespace
DesktopSource::DesktopSource(const Json::Value& config) : config_(config) {
  width = config["output_width"].asInt(); height = config["output_height"].asInt(); rate = config["fps"].asInt();
  if (width < 2 || height < 2 || width > 1920 || height > 1080 || width % 2 || height % 2 || rate < 1 || rate > 60)
    throw std::runtime_error("Invalid desktop encoding dimensions/frame rate");
}
DesktopSource::~DesktopSource() {
  stopping_ = true;
  if (job_) TerminateJobObject(job_, 0);
  if (process_) WaitForSingleObject(process_, 5000);
  if (reader_.joinable()) { CancelSynchronousIo(reader_.native_handle()); reader_.join(); }
  if (logger_.joinable()) { CancelSynchronousIo(logger_.native_handle()); logger_.join(); }
  for (HANDLE handle : {output_, errors_, process_, job_}) if (handle) CloseHandle(handle);
}
void DesktopSource::Start() {
  auto args = cast_to_tv::DesktopCommand(config_, width, height, rate);
  std::wstring executable = Wide(args.front()), command;
  for (const auto& arg : args) { if (!command.empty()) command += L' '; command += Quote(Wide(arg)); }
  SECURITY_ATTRIBUTES attributes{sizeof(attributes), nullptr, TRUE};
  HANDLE write_output = nullptr, write_errors = nullptr, input = nullptr;
  PROCESS_INFORMATION process{};
  std::vector<uint8_t> attribute_storage;
  LPPROC_THREAD_ATTRIBUTE_LIST list = nullptr;
  try {
    if (!CreatePipe(&output_, &write_output, &attributes, 65536) ||
        !CreatePipe(&errors_, &write_errors, &attributes, 16384)) throw WindowsError("CreatePipe");
    if (!SetHandleInformation(output_, HANDLE_FLAG_INHERIT, 0) ||
        !SetHandleInformation(errors_, HANDLE_FLAG_INHERIT, 0)) throw WindowsError("SetHandleInformation");
    input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &attributes, OPEN_EXISTING, 0, nullptr);
    if (input == INVALID_HANDLE_VALUE) { input = nullptr; throw WindowsError("Open NUL"); }
    SIZE_T size = 0; InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
    attribute_storage.resize(size); list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attribute_storage.data());
    if (!InitializeProcThreadAttributeList(list, 1, 0, &size)) { list = nullptr; throw WindowsError("Initialize process attributes"); }
    HANDLE inherited[]{input, write_output, write_errors};
    if (!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherited, sizeof(inherited), nullptr, nullptr))
      throw WindowsError("Restrict inherited handles");
    STARTUPINFOEXW startup{}; startup.StartupInfo.cb = sizeof(startup); startup.lpAttributeList = list;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = input; startup.StartupInfo.hStdOutput = write_output; startup.StartupInfo.hStdError = write_errors;
    job_ = CreateJobObjectW(nullptr, nullptr);
    if (!job_) throw WindowsError("Create capture job");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job_, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) throw WindowsError("Set capture job limits");
    if (!CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, TRUE,
                        CREATE_NO_WINDOW | CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT,
                        nullptr, nullptr, &startup.StartupInfo, &process)) throw WindowsError("Start FFmpeg");
    process_ = process.hProcess;
    if (!AssignProcessToJobObject(job_, process_)) { TerminateProcess(process_, 1); throw WindowsError("Own FFmpeg capture job"); }
    if (ResumeThread(process.hThread) == DWORD(-1)) throw WindowsError("Resume FFmpeg");
    CloseHandle(process.hThread); process.hThread = nullptr;
    DeleteProcThreadAttributeList(list); list = nullptr;
    for (HANDLE handle : {input, write_output, write_errors}) CloseHandle(handle);
    input = write_output = write_errors = nullptr;
    reader_ = std::thread([this] { ReadFrames(); });
    logger_ = std::thread([this] {
      char buffer[4096]; DWORD count = 0;
      while (!stopping_ && ReadFile(errors_, buffer, sizeof(buffer), &count, nullptr) && count)
        std::cerr.write(buffer, count).flush();
    });
  } catch (...) {
    if (list) DeleteProcThreadAttributeList(list);
    if (process.hThread) CloseHandle(process.hThread);
    for (HANDLE handle : {input, write_output, write_errors}) if (handle) CloseHandle(handle);
    throw;
  }
}
bool DesktopSource::ReadExact(void* data, DWORD size, bool allow_eof) {
  DWORD total = 0;
  while (total < size) {
    DWORD count = 0;
    if (stopping_) return false;
    if (!ReadFile(output_, static_cast<char*>(data) + total, size - total, &count, nullptr) || !count) {
      if (stopping_ || (allow_eof && !total)) return false;
      throw std::runtime_error("FFmpeg ended with an incomplete IVF frame");
    }
    total += count;
  }
  return true;
}
void DesktopSource::ReadFrames() {
  try {
    uint8_t header[32]{};
    if (!ReadExact(header, 32)) return;
    if (std::string(reinterpret_cast<char*>(header), 4) != "DKIF" ||
        std::string(reinterpret_cast<char*>(header + 8), 4) != "VP80" ||
        Little(header + 12, 2) != width || Little(header + 14, 2) != height ||
        Little(header + 16, 4) != rate || Little(header + 20, 4) != scale)
      throw std::runtime_error("FFmpeg produced an unexpected IVF header");
    bool first = true; uint64_t previous = 0;
    while (!stopping_) {
      uint8_t frame[12]{};
      if (!ReadExact(frame, 12, true)) break;
      uint64_t length = Little(frame, 4), pts = Little(frame + 4, 8);
      if (!length || length > 4 * 1024 * 1024 || (!first && pts <= previous))
        throw std::runtime_error("Invalid captured IVF frame");
      std::vector<uint8_t> data(length);
      if (!ReadExact(data.data(), DWORD(length))) break;
      std::lock_guard lock(mutex_);
      auto arrival = openscreen::Clock::now();
      if (frames_.size() == 4) { replaced_ += frames_.size(); frames_.clear(); }
      frames_.push_back({pts, arrival, std::move(data)}); previous = pts; first = false;
    }
    if (!stopping_) {
      WaitForSingleObject(process_, 5000); DWORD code = 1;
      GetExitCodeProcess(process_, &code);
      if (code) throw std::runtime_error("Desktop FFmpeg exited with code " + std::to_string(code));
    }
  } catch (const std::exception& error) {
    std::lock_guard lock(mutex_); error_ = error.what();
  }
  std::lock_guard lock(mutex_); finished_ = true;
}
bool DesktopSource::Next(uint64_t& pts, std::vector<uint8_t>& data) {
  std::lock_guard lock(mutex_);
  if (!error_.empty()) throw std::runtime_error(error_);
  if (frames_.empty()) return false;
  auto frame = std::move(frames_.front()); frames_.pop_front();
  data = std::move(frame.data); pts = frame.pts; frame_time_ = frame.time; return true;
}
bool DesktopSource::Finished() { std::lock_guard lock(mutex_); return finished_; }
openscreen::Clock::time_point DesktopSource::FrameTime() { std::lock_guard lock(mutex_); return frame_time_; }
