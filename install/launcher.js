#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const CONFIG_DIR = path.join(os.homedir(), ".instagram-agent");
const MANIFEST_PATH = path.join(CONFIG_DIR, "install.json");

function readManifest() {
  try {
    return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  } catch {
    return null;
  }
}

function defaultInstallDir() {
  return path.resolve(__dirname, "..");
}

function resolveRuntime() {
  const manifest = readManifest();
  const installDir = manifest?.installDir ? path.resolve(manifest.installDir) : defaultInstallDir();
  const pythonPath = manifest?.pythonPath
    ? path.resolve(manifest.pythonPath)
    : process.platform === "win32"
      ? path.join(installDir, ".venv", "Scripts", "python.exe")
      : path.join(installDir, ".venv", "bin", "python3");
  const mainPath = path.join(installDir, "src", "main.py");

  return { installDir, pythonPath, mainPath };
}

function main() {
  const { installDir, pythonPath, mainPath } = resolveRuntime();

  if (!fs.existsSync(mainPath)) {
    console.error(`[instagram] Could not find main.py at ${mainPath}`);
    process.exit(1);
  }

  const result = spawnSync(pythonPath, [mainPath, ...process.argv.slice(2)], {
    stdio: "inherit",
    cwd: installDir,
    env: {
      ...process.env,
      INSTAGRAM_AGENT_HOME: installDir,
      INSTAGRAM_AGENT_MANIFEST: MANIFEST_PATH,
    },
  });

  if (result.error) {
    console.error(`[instagram] Failed to launch Python runtime at ${pythonPath}`);
    console.error(result.error.message);
    process.exit(1);
  }

  process.exit(result.status ?? 0);
}

main();
