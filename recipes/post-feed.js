const path = require("node:path");

const {
  buildArgs,
  getInputValue,
  mediaTypeForPath,
  normalizeOptionalText,
  normalizeText,
  parseMediaId,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const filePath = path.resolve(normalizeText(getInputValue(input, "path"), "Instagram feed path"));
  const caption = normalizeOptionalText(getInputValue(input, "caption"));

  const args = buildArgs(input, "post-feed", ["--path", filePath, caption ? ["--caption", caption] : []]);
  const result = await runInstagramCommand(context, args, 300000);
  const mediaId = parseMediaId(result.stdout);
  const mediaType = mediaTypeForPath(filePath);

  return {
    status: mediaId ? "ok" : "partial",
    reply: `Instagram feed post published for ${path.basename(filePath)}${caption ? ` with caption: ${caption}` : ""}. Media ID: ${mediaId || "unavailable"}.`,
    metadata: {
      instagram: {
        operation: "post-feed",
        path: filePath,
        fileName: path.basename(filePath),
        mediaType,
        caption,
        mediaId,
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
