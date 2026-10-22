#include "control/protocol.h"
#include <iostream>
#include <memory>
#include <stdexcept>

namespace cast_to_tv {
Json::Value Parse(const std::string& text) {
  Json::CharReaderBuilder builder;
  std::unique_ptr<Json::CharReader> reader(builder.newCharReader());
  Json::Value value; std::string errors;
  if (!reader->parse(text.data(), text.data() + text.size(), &value, &errors))
    throw std::runtime_error("Invalid control JSON: " + errors);
  return value;
}
void Emit(Json::Value event) {
  event["version"] = 1;
  Json::StreamWriterBuilder builder; builder["indentation"] = "";
  std::cout << Json::writeString(builder, event) << std::endl;
}
void StdioPort::SetClient(Client& client) { client_ = &client; }

void StdioPort::ResetClient() { client_ = nullptr; }

void StdioPort::PostMessage(const std::string& destination, const std::string& ns, const std::string& body) {
  Json::Value event; event["event"] = "message"; event["destination"] = destination;
  event["namespace"] = ns; event["payload"] = Parse(body); Emit(event);
}

void StdioPort::Receive(const Json::Value& message) {
  if (!client_) return;
  Json::StreamWriterBuilder builder; builder["indentation"] = "";
  client_->OnMessage(message["source"].asString(), message["namespace"].asString(),
                     Json::writeString(builder, message["payload"]));
}
}  // namespace cast_to_tv
