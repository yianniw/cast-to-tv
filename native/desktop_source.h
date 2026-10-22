#pragma once
#include <windows.h>
#include <atomic>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>
#include "json/value.h"
#include "platform/api/time.h"

// Capture and VP8 encoding run in a child owned by the helper's Windows job.
// At most four consecutive encoded frames are retained. No frames cross Python.
class DesktopSource {
 public:
  explicit DesktopSource(const Json::Value& config);
  ~DesktopSource();
  void Start();
  bool Next(uint64_t& pts, std::vector<uint8_t>& data);
  bool Finished();
  openscreen::Clock::time_point FrameTime();
  int width, height, rate, scale = 1;
  uint64_t Replaced() const { return replaced_; }
 private:
  void ReadFrames();
  bool ReadExact(void* data, DWORD size, bool allow_eof = false);
  Json::Value config_;
  std::mutex mutex_;
  struct CapturedFrame { uint64_t pts; openscreen::Clock::time_point time; std::vector<uint8_t> data; };
  std::deque<CapturedFrame> frames_;
  openscreen::Clock::time_point frame_time_{};
  std::string error_;
  bool finished_ = false;
  std::atomic<bool> stopping_{false};
  std::atomic<uint64_t> replaced_{0};
  HANDLE process_ = nullptr, job_ = nullptr, output_ = nullptr, errors_ = nullptr;
  std::thread reader_, logger_;
};
