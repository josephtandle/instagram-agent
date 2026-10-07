const {
  buildArgs,
  getInputValue,
  normalizeHandle,
  normalizeText,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const handle = normalizeHandle(getInputValue(input, "handle"), "Instagram handle");
  const text = normalizeText(getInputValue(input, "text"), "Message text");

  const nameRaw = getInputValue(input, "name");
  const name = nameRaw ? String(nameRaw).trim() : "";
  // main.py send-dm runs the shared close-circle guard before sending.
  const args = buildArgs(input, "send-dm", [handle, "--text", text, name ? ["--name", name] : undefined]);
  const result = await runInstagramCommand(context, args, 60000);

  let parsed = {};
  try {
    parsed = JSON.parse(result.stdout);
  } catch (_) {}

  return {
    status: parsed.sent ? "ok" : "error",
    reply: parsed.sent
      ? `DM sent to @${handle}: "${text}"`
      : parsed.blocked
        ? `NOT SENT to @${handle}: ${parsed.error}`
        : `Failed to send DM to @${handle}: ${parsed.error || result.stderr || "unknown error"}`,
    metadata: {
      instagram: {
        operation: "send-dm",
        handle,
        text,
        threadId: parsed.thread_id,
        sent: parsed.sent ?? false,
        closeCircleBlocked: Boolean(parsed.blocked),
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
