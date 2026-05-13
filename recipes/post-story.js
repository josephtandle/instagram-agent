const path = require("node:path");

const {
  buildArgs,
  getInputValue,
  mediaTypeForPath,
  normalizeHandleList,
  normalizeOptionalText,
  normalizeText,
  parseMediaId,
  parseStoryTagLines,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const filePath = path.resolve(normalizeText(getInputValue(input, "path"), "Instagram story path"));
  const rawTagHandles = getInputValue(input, "tagHandles", []);
  const tagHandles = Array.isArray(rawTagHandles) && rawTagHandles.length > 0
    ? normalizeHandleList(rawTagHandles, "Instagram story tag")
    : [];

  const args = buildArgs(input, "post-story", [
    "--path",
    filePath,
    tagHandles.length > 0 ? ["--tag", tagHandles.map((handle) => `@${handle}`)] : [],
  ]);
  const result = await runInstagramCommand(context, args, 300000);
  const mediaId = parseMediaId(result.stdout);
  const { resolvedTags, failedTags } = parseStoryTagLines(`${result.stdout}\n${result.stderr}`);
  const status = failedTags.length > 0 ? "partial" : "ok";

  return {
    status,
    reply: `Instagram story posted for ${path.basename(filePath)}. Media ID: ${mediaId || "unavailable"}${failedTags.length > 0 ? `; ${failedTags.length} tag(s) could not be resolved.` : ""}`,
    metadata: {
      instagram: {
        operation: "post-story",
        path: filePath,
        fileName: path.basename(filePath),
        mediaType: mediaTypeForPath(filePath),
        requestedTags: tagHandles,
        resolvedTags,
        failedTags,
        mediaId,
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
