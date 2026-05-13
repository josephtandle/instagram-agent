const {
  buildArgs,
  getInputValue,
  normalizeHandleList,
  parseResolveUserLines,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const handles = normalizeHandleList(getInputValue(input, "handles"), "Instagram username");
  const args = buildArgs(input, "resolve-user", handles.map((handle) => `@${handle}`));
  const result = await runInstagramCommand(context, args, 120000);
  const { resolved, errors } = parseResolveUserLines(`${result.stdout}\n${result.stderr}`);
  const status = errors.length > 0 ? "partial" : "ok";

  return {
    status,
    reply: resolved.length > 0
      ? `Resolved ${resolved.length} Instagram username(s).${errors.length > 0 ? ` ${errors.length} failed.` : ""}`
      : "No Instagram usernames were resolved.",
    metadata: {
      instagram: {
        operation: "resolve-user",
        handles,
        resolved,
        errors,
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
