const {
  buildArgs,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const includeMedia = Boolean(input?.includeMedia || input?.args?.includeMedia);
  const args = buildArgs(input, "official-stats", includeMedia ? ["--include-media"] : []);
  const result = await runInstagramCommand(context, args, 180000);

  let parsed = {};
  try {
    parsed = JSON.parse(result.stdout);
  } catch (error) {
    throw new Error(`Instagram official stats command returned invalid JSON: ${error.message}`);
  }

  const yesterday = parsed.windows?.yesterday;
  const status = parsed.status === "ok" ? "ok" : "partial";
  const reply = parsed.status === "ok"
    ? `Instagram official stats fetched: yesterday ${Number(yesterday?.views || 0).toLocaleString()} views, ${Number(yesterday?.comments || 0).toLocaleString()} comments.`
    : `Instagram official stats unavailable: ${parsed.error || "missing Meta Graph configuration"}`;

  return {
    status,
    reply,
    metadata: {
      instagram: {
        operation: "official-stats",
        source: parsed.source || "meta_graph_api",
        readOnly: true,
        stats: parsed,
        command: ["python3", ...args],
        stderr: result.stderr.trim(),
      },
    },
  };
};
