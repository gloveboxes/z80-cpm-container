const port = process.argv[2] || "8081";
const socket = new WebSocket(`ws://127.0.0.1:${port}/ws`);
socket.binaryType = "arraybuffer";
socket.onopen = () => console.log(JSON.stringify({event: "open"}));
socket.onmessage = event => {
  const data = typeof event.data === "string" ? event.data : Buffer.from(event.data).toString("latin1");
  console.log(JSON.stringify({event: "data", data}));
};
socket.onerror = () => console.log(JSON.stringify({event: "error"}));
socket.onclose = event => {
  console.log(JSON.stringify({event: "close", code: event.code}));
  process.exit(0);
};
process.stdin.setEncoding("utf8");
let pending = "";
process.stdin.on("data", chunk => {
  pending += chunk;
  while (pending.includes("\n")) {
    const index = pending.indexOf("\n");
    const command = JSON.parse(pending.slice(0, index));
    pending = pending.slice(index + 1);
    if (command.send) socket.send(Buffer.from(command.send, "latin1"));
    if (command.close) socket.close();
  }
});
