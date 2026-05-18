const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { RecipeInputError } = require("../../shared/recipe-errors");

const AGENT_ROOT = path.resolve(__dirname, "..");
const MAIN_SCRIPT = path.join(AGENT_ROOT, "src", "main.py");
const MANIFEST_PATH = path.join(os.homedir(), ".instagram-agent", "install.json");

function _readManifest() {
  try {
    return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  } catch {
    return null;
  }
}

const _manifest = _readManifest();
const PYTHON_BIN = _manifest?.pythonPath || process.env.PYTHON3 || "python3";
const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".avi", ".mkv"]);

function getInputValue(input, key, fallback = undefined) {
  if (input && Object.prototype.hasOwnProperty.call(input, key)) {
    return input[key];
  }
  if (input?.args && Object.prototype.hasOwnProperty.call(input.args, key)) {
    return input.args[key];
  }
  return fallback;
}

function normalizeText(value, label) {
  const text = String(value ?? "").trim();
  if (!text) {
    throw new RecipeInputError(`${label} is required`);
  }
  return text;
}

function normalizeOptionalText(value) {
  const text = String(value ?? "").trim();
  return text || "";
}

function normalizeHandle(value, label = "Instagram handle") {
  return normalizeText(value, label).replace(/^@+/, "");
}

function normalizeHandleList(values, label = "Instagram handle") {
  if (!Array.isArray(values) || values.length === 0) {
    throw new RecipeInputError(`At least one ${label.toLowerCase()} is required`);
  }

  return values.map((value) => normalizeHandle(value, label));
}

function normalizePathList(values, label = "file path") {
  if (!Array.isArray(values) || values.length === 0) {
    throw new RecipeInputError(`At least one ${label} is required`);
  }

  return values.map((value) => {
    const filePath = normalizeText(value, label);
    const resolved = path.resolve(filePath);
    if (!fs.existsSync(resolved)) {
      throw new RecipeInputError(`File not found: ${resolved}`);
    }
    return resolved;
  });
}

function normalizePostsCount(value, fallback = 12) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }

  const count = Number(value);
  if (!Number.isInteger(count) || count < 1) {
    throw new RecipeInputError("posts must be a positive integer");
  }

  return Math.min(count, 20);
}

function mediaTypeForPath(filePath) {
  return VIDEO_EXTENSIONS.has(path.extname(filePath).toLowerCase()) ? "video" : "image";
}

function buildArgs(input, command, commandArgs = []) {
  const args = [MAIN_SCRIPT];
  const username = normalizeOptionalText(getInputValue(input, "username")).replace(/^@+/, "");

  if (username) {
    args.push("--username", username);
  }

  args.push(command);
  for (const value of commandArgs) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const entry of value) {
        if (entry === undefined || entry === null || entry === "") continue;
        args.push(String(entry));
      }
      continue;
    }
    args.push(String(value));
  }

  return args;
}

async function runInstagramCommand(context, args, timeoutMs = 180000) {
  const result = await context.runProcess(PYTHON_BIN, args, {
    cwd: AGENT_ROOT,
    timeoutMs,
  });

  if (result.code !== 0) {
    throw new Error((result.stderr || result.stdout || "Instagram command failed").trim());
  }

  return {
    stdout: String(result.stdout || ""),
    stderr: String(result.stderr || ""),
    code: result.code,
    signal: result.signal,
  };
}

function parseMediaId(output) {
  const match = String(output || "").match(/Media ID:\s*([^\s]+)/i);
  return match ? match[1] : "";
}

function parseResolveUserLines(output) {
  const resolved = [];
  const errors = [];
  const lines = String(output || "").split(/\r?\n/);

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    let match = line.match(/^@?([^\s→]+)\s+→\s+ERROR:\s*(.+)$/i);
    if (match) {
      errors.push({
        handle: match[1].replace(/^@+/, ""),
        error: match[2].trim(),
      });
      continue;
    }

    match = line.match(/^@?([^\s→]+)\s+→\s+(.+)$/i);
    if (match) {
      resolved.push({
        handle: match[1].replace(/^@+/, ""),
        userId: match[2].trim(),
      });
    }
  }

  return { resolved, errors };
}

function parseStoryTagLines(output) {
  const resolvedTags = [];
  const failedTags = [];
  const lines = String(output || "").split(/\r?\n/);

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    let match = line.match(/^✓\s*@?([^\s→]+)\s+→\s+(.+)$/i);
    if (match) {
      resolvedTags.push({
        handle: match[1].replace(/^@+/, ""),
        userId: match[2].trim(),
      });
      continue;
    }

    match = line.match(/^✗\s*@?([^:]+):\s*(.+)$/i);
    if (match) {
      failedTags.push({
        handle: match[1].trim().replace(/^@+/, ""),
        error: match[2].trim(),
      });
    }
  }

  return { resolvedTags, failedTags };
}

function parseProfileJson(output) {
  const text = String(output || "").trim();
  if (!text) {
    throw new Error("Instagram profile command returned no JSON output");
  }

  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`Instagram profile command returned invalid JSON: ${error.message}`);
  }
}

function formatCount(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

module.exports = {
  AGENT_ROOT,
  buildArgs,
  formatCount,
  getInputValue,
  mediaTypeForPath,
  normalizeHandle,
  normalizeHandleList,
  normalizeOptionalText,
  normalizePathList,
  normalizePostsCount,
  normalizeText,
  parseMediaId,
  parseProfileJson,
  parseResolveUserLines,
  parseStoryTagLines,
  runInstagramCommand,
  PYTHON_BIN,
};
