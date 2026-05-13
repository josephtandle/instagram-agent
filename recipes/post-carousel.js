const {
  buildArgs,
  getInputValue,
  normalizeOptionalText,
  normalizePathList,
  parseMediaId,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const paths = normalizePathList(getInputValue(input, "paths"), "carousel image path");
  if (paths.length > 10) {
    throw new Error("Carousel posts support at most 10 images");
  }

  const caption = normalizeOptionalText(getInputValue(input, "caption"));
  const args = buildArgs(input, "post-carousel", [
    "--paths",
    paths,
    caption ? ["--caption", caption] : [],
  ]);
  const result = await runInstagramCommand(context, args, 300000);
  const mediaId = parseMediaId(result.stdout);

  return {
    status: mediaId ? "ok" : "partial",
    reply: `Instagram carousel posted with ${paths.length} image(s)${caption ? ` and caption: ${caption}` : ""}. Media ID: ${mediaId || "unavailable"}.`,
    metadata: {
      instagram: {
        operation: "post-carousel",
        paths,
        caption,
        mediaId,
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
