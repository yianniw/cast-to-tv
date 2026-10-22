#include "windows_platform.h"

void WindowsTaskRunner::PostPackagedTask(Task task) {
  PostPackagedTaskWithDelay(std::move(task), openscreen::Clock::duration::zero());
}
void WindowsTaskRunner::PostPackagedTaskWithDelay(Task task, openscreen::Clock::duration delay) {
  std::lock_guard lock(mutex_);
  if (stopped_) return;
  tasks_.emplace(std::make_pair(openscreen::Clock::now() + delay, sequence_++), std::move(task));
  changed_.notify_one();
}
bool WindowsTaskRunner::IsRunningOnTaskRunner() { return owner_ == std::this_thread::get_id(); }
void WindowsTaskRunner::Stop() { std::lock_guard lock(mutex_); stopped_ = true; changed_.notify_all(); }
void WindowsTaskRunner::Run() {
  std::unique_lock lock(mutex_);
  while (!stopped_) {
    if (tasks_.empty()) { changed_.wait(lock); continue; }
    auto due = tasks_.begin()->first.first;
    auto now = openscreen::Clock::now();
    if (due > now) { changed_.wait_for(lock, due - now); continue; }
    auto task = std::move(tasks_.begin()->second); tasks_.erase(tasks_.begin());
    auto result = task.get_future();
    lock.unlock(); task(); result.get(); lock.lock();
  }
}
