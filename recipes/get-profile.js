const {
  buildArgs,
  formatCount,
  getInputValue,
  normalizeHandle,
  normalizePostsCount,
  parseProfileJson,
  runInstagramCommand,
} = require("./_instagram-cli");

module.exports.runRecipe = async function runRecipe(input, context) {
  const handle = normalizeHandle(getInputValue(input, "handle"), "Instagram profile handle");
  const requestedPosts = normalizePostsCount(getInputValue(input, "posts"), 12);
  const args = buildArgs(input, "get-profile", [`@${handle}`, "--posts", String(requestedPosts)]);
  const result = await runInstagramCommand(context, args, 240000);
  const profile = parseProfileJson(result.stdout);

  return {
    status: "ok",
    reply: `Instagram profile fetched for @${handle}: ${formatCount(profile.followers)} followers, ${formatCount(profile.following)} following, ${formatCount(profile.post_count)} posts, ${formatCount(profile.posts_fetched)} recent posts.`,
    metadata: {
      instagram: {
        operation: "get-profile",
        handle,
        requestedPosts,
        profile,
        command: ["python3", ...args],
        stdout: result.stdout.trim(),
        stderr: result.stderr.trim(),
      },
    },
  };
};
