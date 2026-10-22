#pragma once
#include <condition_variable>
#include <map>
#include <mutex>
#include <thread>
#include "platform/api/task_runner.h"

// Runs every Open Screen callback serially on the owning thread.
class WindowsTaskRunner final : public openscreen::TaskRunner {
 public:
  void PostPackagedTask(Task task) override;
  void PostPackagedTaskWithDelay(Task task, openscreen::Clock::duration delay) override;
  bool IsRunningOnTaskRunner() override;
  void Run();
  void Stop();
 private:
  std::mutex mutex_;
  std::condition_variable changed_;
  std::map<std::pair<openscreen::Clock::time_point, uint64_t>, Task> tasks_;
  uint64_t sequence_ = 0;
  bool stopped_ = false;
  std::thread::id owner_ = std::this_thread::get_id();
};
