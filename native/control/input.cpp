#include <windows.h>
#include <algorithm>
#include <stdexcept>
#include "control/input.h"
#include "control/protocol.h"
#include "sender/probe.h"

using namespace openscreen;
using namespace openscreen::cast;
using namespace std::chrono_literals;

namespace cast_to_tv {
ControlInput::ControlInput(WindowsTaskRunner& runner, StdioPort& port, Probe& probe)
    : runner_(runner), port_(port), probe_(probe) {}

void ControlInput::Poll() {
  try {
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE); DWORD available = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) { probe_.Stop(); return; }
    if (available) {
      char buffer[4096]; DWORD count = 0;
      if (!ReadFile(input, buffer, std::min<DWORD>(available, sizeof(buffer)), &count, nullptr)) { probe_.Stop(); return; }
      pending_.append(buffer, count);
      if (pending_.size() > 1024 * 1024) throw std::runtime_error("Control message exceeds limit");
      for (size_t newline; (newline = pending_.find('\n')) != std::string::npos;) {
        auto line = pending_.substr(0, newline); pending_.erase(0, newline + 1);
        auto message = Parse(line);
        if (message["version"].asInt() != 1) throw std::runtime_error("Unsupported control version");
        if (message["command"] == "stop") { probe_.Stop(); return; }
        if (message["command"] == "message") port_.Receive(message);
        else throw std::runtime_error("Unknown control command");
      }
    }
    runner_.PostTaskWithDelay([this] { Poll(); }, 10ms);
  } catch (const std::exception& error) { probe_.Fail(error.what()); }
}
}  // namespace cast_to_tv
