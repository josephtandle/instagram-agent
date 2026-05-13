const {
  buildArgs,
  getInputValue,
  normalizeText,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const media = normalizeText(getInputValue(input, "media"), "Media reference");
  const text = normalizeText(getInputValue(input, "text"), "Reply text");
  const commentId = String(getInputValue(input, "commentId", getInputValue(input, "comment_id", "")) || "").trim();

  const args = buildArgs(input, "reply-comment", [
    media,
    "--text",
    text,
    commentId ? ["--comment-id", commentId] : [],
  ]);
  const result = await runInstagramCommand(context, args, 90000);

  let parsed = {};
  try {
    parsed = JSON.parse(result.stdout);
  } catch (_) {}

  return {
    status: parsed.sent ? "ok" : "error",
    reply: parsed.sent
      ? `Instagram comment posted on ${media}${commentId ? ` in reply to ${commentId}` : ""}.`
      : `Failed to post Instagram comment on ${media}: ${parsed.error || result.stderr || "unknown error"}`,
    metadata: {
      instagram: {
        operation: "reply-comment",
        media,
        text,
        commentId,
        sent: parsed.sent ?? false,
        mediaId: parsed.media_id || "",
        postedCommentId: parsed.comment_id || "",
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
