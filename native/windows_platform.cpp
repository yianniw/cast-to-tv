// Open Screen clock and diagnostic logging hooks supplied by the embedding app.
#include <cstdlib>
#include <iostream>
#include "platform/api/logging.h"
#include "platform/api/time.h"

namespace openscreen {
Clock::time_point Clock::now() noexcept {
  return time_point(std::chrono::duration_cast<duration>(
      std::chrono::steady_clock::now().time_since_epoch()));
}
std::chrono::seconds GetWallTimeSinceUnixEpoch() noexcept {
  return std::chrono::duration_cast<std::chrono::seconds>(
      std::chrono::system_clock::now().time_since_epoch());
}
bool IsLoggingOn(LogLevel level, std::string_view) { return level >= LogLevel::kWarning; }
void LogWithLevel(LogLevel, const char* file, int line, std::stringstream message) {
  std::cerr << file << ':' << line << ' ' << message.str() << std::endl;
}
[[noreturn]] void Break() { std::abort(); }

}  // namespace openscreen
