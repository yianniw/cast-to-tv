#include <winsock2.h>
#include <ws2tcpip.h>
#include <algorithm>
#include <atomic>
#include <limits>
#include <memory>
#include <thread>
#include "platform/api/task_runner.h"
#include "platform/api/udp_socket.h"

namespace openscreen {
namespace {
Error SocketError(Error::Code code, const char* operation) {
  return Error(code, std::string(operation) + ": Winsock " + std::to_string(WSAGetLastError()));
}
sockaddr_in Address(const IPEndpoint& endpoint) {
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(endpoint.port);
  endpoint.address.CopyToV4(reinterpret_cast<uint8_t*>(&address.sin_addr));
  return address;
}
IPEndpoint Endpoint(const sockaddr_in& address) {
  return {IPAddress(IPAddress::Version::kV4,
                    reinterpret_cast<const uint8_t*>(&address.sin_addr)), ntohs(address.sin_port)};
}
class WindowsUdpSocket final : public UdpSocket {
 public:
  WindowsUdpSocket(TaskRunner& runner, Client* client, IPEndpoint local, SOCKET socket)
      : runner_(runner), local_(local), socket_(socket), state_(std::make_shared<State>()) {
    state_->client = client;
  }
  ~WindowsUdpSocket() override {
    state_->alive = false;
    if (reader_.joinable()) reader_.join();
    closesocket(socket_);
  }
  bool IsIPv4() const override { return true; }
  bool IsIPv6() const override { return false; }
  IPEndpoint GetLocalEndpoint() const override { return local_; }
  void Bind() override {
    auto address = Address(local_);
    if (bind(socket_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR) {
      Notify(SocketError(Error::Code::kSocketBindFailure, "bind")); return;
    }
    int length = sizeof(address);
    if (getsockname(socket_, reinterpret_cast<sockaddr*>(&address), &length) == SOCKET_ERROR) {
      Notify(SocketError(Error::Code::kSocketBindFailure, "getsockname")); return;
    }
    local_ = Endpoint(address);
    auto state = state_;
    runner_.PostTask([state, this] { if (state->alive) state->client->OnBound(this); });
    reader_ = std::thread([this] { ReadLoop(); });
  }
  void SetMulticastOutboundInterface(NetworkInterfaceIndex) override {
    Notify(Error(Error::Code::kNotImplemented, "Multicast is not used by this sender"));
  }
  void JoinMulticastGroup(const IPAddress&, NetworkInterfaceIndex) override {
    Notify(Error(Error::Code::kNotImplemented, "Multicast is not used by this sender"));
  }
  void SendMessage(ByteView data, const IPEndpoint& destination) override {
    if (!destination.address.IsV4()) {
      Notify(Error(Error::Code::kSocketSendFailure, "IPv6 is not supported by this probe"), true); return;
    }
    auto address = Address(destination);
    int sent = sendto(socket_, reinterpret_cast<const char*>(data.data()), static_cast<int>(data.size()),
                      0, reinterpret_cast<sockaddr*>(&address), sizeof(address));
    if (sent == SOCKET_ERROR) {
      auto code = WSAGetLastError() == WSAEWOULDBLOCK ? Error::Code::kAgain : Error::Code::kSocketSendFailure;
      Notify(SocketError(code, "sendto"), true);
    }
  }
  void SetDscp(DscpMode mode) override {
    int tos = static_cast<int>(mode) << 2;
    if (setsockopt(socket_, IPPROTO_IP, IP_TOS, reinterpret_cast<char*>(&tos), sizeof(tos)))
      Notify(SocketError(Error::Code::kSocketOptionSettingFailure, "IP_TOS"));
  }
  void SetReceiveBufferSize(size_t size) override { SetBuffer(SO_RCVBUF, size); }
  void SetSendBufferSize(size_t size) override { SetBuffer(SO_SNDBUF, size); }
 private:
  struct State { std::atomic<bool> alive{true}; Client* client = nullptr; };
  void Notify(Error error, bool send = false) {
    auto state = state_;
    runner_.PostTask([state, this, error = std::move(error), send] {
      if (!state->alive) return;
      if (send) state->client->OnSendError(this, error); else state->client->OnError(this, error);
    });
  }
  void SetBuffer(int option, size_t size) {
    int value = static_cast<int>(std::min(size, static_cast<size_t>(std::numeric_limits<int>::max())));
    if (setsockopt(socket_, SOL_SOCKET, option, reinterpret_cast<char*>(&value), sizeof(value)))
      Notify(SocketError(Error::Code::kSocketOptionSettingFailure, "socket buffer"));
  }
  void ReadLoop() {
    while (state_->alive) {
      fd_set readable; FD_ZERO(&readable); FD_SET(socket_, &readable);
      timeval timeout{0, 50000};
      int ready = select(0, &readable, nullptr, nullptr, &timeout);
      if (ready == 0) continue;
      if (ready == SOCKET_ERROR) { Notify(SocketError(Error::Code::kSocketReadFailure, "select")); return; }
      UdpPacket packet(UdpPacket::kUdpMaxPacketSize);
      sockaddr_in from{}; int length = sizeof(from);
      int count = recvfrom(socket_, reinterpret_cast<char*>(packet.data()), static_cast<int>(packet.size()),
                           0, reinterpret_cast<sockaddr*>(&from), &length);
      if (count == SOCKET_ERROR) {
        if (WSAGetLastError() == WSAEWOULDBLOCK || WSAGetLastError() == WSAECONNRESET) continue;
        Notify(SocketError(Error::Code::kSocketReadFailure, "recvfrom")); return;
      }
      packet.resize(count); packet.set_source(Endpoint(from)); packet.set_destination(local_);
      auto state = state_;
      runner_.PostTask([state, this, packet = std::move(packet)]() mutable {
        if (state->alive) state->client->OnRead(this, std::move(packet));
      });
    }
  }
  TaskRunner& runner_;
  IPEndpoint local_;
  SOCKET socket_;
  std::shared_ptr<State> state_;
  std::thread reader_;
};
}  // namespace
ErrorOr<std::unique_ptr<UdpSocket>> UdpSocket::Create(
    TaskRunner& runner, Client* client, const IPEndpoint& local) {
  if (!local.address.IsV4()) return Error(Error::Code::kNotImplemented, "IPv4 probe only");
  static const int startup = [] { WSADATA data{}; return WSAStartup(MAKEWORD(2, 2), &data); }();
  if (startup) return Error(Error::Code::kSocketInvalidState, "WSAStartup failed");
  SOCKET socket = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (socket == INVALID_SOCKET) return SocketError(Error::Code::kSocketInvalidState, "socket");
  u_long nonblocking = 1;
  if (ioctlsocket(socket, FIONBIO, &nonblocking)) {
    auto error = SocketError(Error::Code::kSocketInvalidState, "nonblocking"); closesocket(socket); return error;
  }
  return std::unique_ptr<UdpSocket>(new WindowsUdpSocket(runner, client, local, socket));
}
}  // namespace openscreen
