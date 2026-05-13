const {
  buildArgs,
  getInputValue,
  normalizeText,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const media = normalizeText(getInputValue(input, "media"), "Media reference");
  const rawLimit = getInputValue(input, "limit", 20);
  const limit = Number.isInteger(Number(rawLimit)) && Number(rawLimit) > 0
    ? Math.min(Number(rawLimit), 100)
    : 20;

  const args = buildArgs(input, "read-comments", [media, "--limit", String(limit)]);
  const result = await runInstagramCommand(context, args, 90000);

  let parsed = {};
  try {
    parsed = JSON.parse(result.stdout);
  } catch (_) {}

  const fetched = Number(parsed.comments_fetched || 0);
  return {
    status: Array.isArray(parsed.comments) ? "ok" : "error",
    reply: Array.isArray(parsed.comments)
      ? `Fetched ${fetched} Instagram comment${fetched === 1 ? "" : "s"} for ${media}.`
      : `Failed to read Instagram comments for ${media}: ${parsed.error || result.stderr || "unknown error"}`,
    metadata: {
      instagram: {
        operation: "read-comments",
        media,
        limit,
        commentsFetched: fetched,
        comments: parsed.comments || [],
        mediaId: parsed.media_id || "",
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
