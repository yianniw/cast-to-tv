#pragma once
#include <string>
// Keep the MessagePort ABI identical with and without Windows headers.
#ifdef SendMessage
#undef SendMessage
#endif
#ifdef PostMessage
#undef PostMessage
#endif
#include "cast/common/public/message_port.h"
#include "json/json.h"

namespace cast_to_tv {
Json::Value Parse(const std::string& text);
void Emit(Json::Value event);

// Python owns TLS; this port carries versioned JSONL over standard input/output.
class StdioPort final : public openscreen::cast::MessagePort {
 public:
  void SetClient(Client& client) override;
  void ResetClient() override;
  void PostMessage(const std::string& destination, const std::string& ns, const std::string& body) override;
  void Receive(const Json::Value& message);
 private:
  Client* client_ = nullptr;
};
}  // namespace cast_to_tv
