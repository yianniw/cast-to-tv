#pragma once
#include <string>
#include "windows_platform.h"

namespace cast_to_tv {
class StdioPort;
class Probe;

// Poll bounded control messages on the same task runner as the sender.
class ControlInput {
 public:
  ControlInput(WindowsTaskRunner& runner, StdioPort& port, Probe& probe);
  void Poll();
 private:
  WindowsTaskRunner& runner_; StdioPort& port_; Probe& probe_; std::string pending_;
};
}  // namespace cast_to_tv
