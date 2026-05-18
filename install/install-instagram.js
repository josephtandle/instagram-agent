#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync, spawnSync } = require("child_process");

const REPO_ROOT = path.resolve(__dirname, "..");
const DEFAULT_INSTALL_DIR = path.join(os.homedir(), "Tools", "Instagram");
const CONFIG_DIR = path.join(os.homedir(), ".instagram-agent");
const MANIFEST_PATH = path.join(CONFIG_DIR, "install.json");
const ENV_TEMPLATE_PATH = path.join(CONFIG_DIR, ".env");

function parseArgs(argv) {
  const args = {
    target: DEFAULT_INSTALL_DIR,
    skipDeps: false,
    skipGlobal: false,
    username: "",
  };

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "--target") args.target = argv[++i];
    else if (token === "--skip-deps") args.skipDeps = true;
    else if (token === "--skip-global") args.skipGlobal = true;
    else if (token === "--username") args.username = argv[++i] || "";
  }

  return args;
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

const COPY_SKIP_NAMES = new Set([".git", "node_modules", ".venv", "__pycache__"]);
const DATA_SKIP_NAMES = new Set(["sessions", "usage.json"]);
const TRANSCRIBER_SKIP_NAMES = new Set(["data"]);

function copyRecursive(sourceDir, destDir, skipNames = COPY_SKIP_NAMES) {
  ensureDir(destDir);
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    if (skipNames.has(entry.name)) continue;

    // status.json is runtime state — never ship it to other users
    if (entry.name === "status.json") continue;

    if (entry.name === "data") {
      const sourceData = path.join(sourceDir, entry.name);
      const destData = path.join(destDir, entry.name);
      ensureDir(destData);
      for (const dataEntry of fs.readdirSync(sourceData, { withFileTypes: true })) {
        if (DATA_SKIP_NAMES.has(dataEntry.name)) continue;
        const src = path.join(sourceData, dataEntry.name);
        const dst = path.join(destData, dataEntry.name);
        if (dataEntry.isDirectory()) copyRecursive(src, dst);
        else fs.copyFileSync(src, dst);
      }
      continue;
    }

    if (entry.name === "transcriber" && entry.isDirectory()) {
      // Copy transcriber but skip its local data cache
      copyRecursive(
        path.join(sourceDir, entry.name),
        path.join(destDir, entry.name),
        new Set([...COPY_SKIP_NAMES, ...TRANSCRIBER_SKIP_NAMES]),
      );
      continue;
    }

    const sourcePath = path.join(sourceDir, entry.name);
    const destPath = path.join(destDir, entry.name);
    if (entry.isDirectory()) {
      copyRecursive(sourcePath, destPath);
    } else {
      ensureDir(path.dirname(destPath));
      fs.copyFileSync(sourcePath, destPath);
    }
  }
}

function fileExists(filePath) {
  try {
    fs.accessSync(filePath);
    return true;
  } catch {
    return false;
  }
}

function isPython310Plus(command) {
  const result = spawnSync(
    command,
    ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
    { encoding: "utf8" },
  );
  return result.status === 0;
}

function findPython() {
  const attempts = process.platform === "win32"
    ? [
        ["py", ["-3", "--version"]],
        ["python", ["--version"]],
      ]
    : [
        ["python3", ["--version"]],
        ["python", ["--version"]],
      ];

  for (const [command, args] of attempts) {
    const result = spawnSync(command, args, { encoding: "utf8" });
    if (result.status !== 0) continue;
    if (!isPython310Plus(command)) {
      throw new Error(
        `Python 3.10+ is required, but the detected ${command} is older.\n` +
        "Install Python 3.10+ via pyenv, Homebrew (brew install python@3.11), or python.org.",
      );
    }
    return command;
  }

  throw new Error("Could not find Python 3. Install Python 3.10+ and try again.");
}

function createVenv(pythonCommand, installDir) {
  const venvDir = path.join(installDir, ".venv");
  if (!fileExists(venvDir)) {
    const args = process.platform === "win32" && pythonCommand === "py"
      ? ["-3", "-m", "venv", venvDir]
      : ["-m", "venv", venvDir];
    execFileSync(pythonCommand, args, { stdio: "inherit" });
  }
  return venvDir;
}

function resolveVenvPython(venvDir) {
  return process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python3");
}

function installDeps(venvPython, installDir) {
  execFileSync(venvPython, ["-m", "pip", "install", "--upgrade", "pip"], {
    stdio: "inherit",
    cwd: installDir,
  });
  execFileSync(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"], {
    stdio: "inherit",
    cwd: installDir,
  });
}

function writeEnvTemplate(username) {
  ensureDir(CONFIG_DIR);
  if (!fileExists(ENV_TEMPLATE_PATH)) {
    const content = [
      `IG_USERNAME=${username || "your-instagram-handle"}`,
      "IG_PASSWORD=replace-me",
      "",
    ].join("\n");
    fs.writeFileSync(ENV_TEMPLATE_PATH, content, "utf8");
  }
}

function writeManifest(installDir, pythonPath) {
  ensureDir(CONFIG_DIR);
  const manifest = {
    agentName: "Instagram",
    installDir,
    pythonPath,
    launcherCommand: "instagram",
    envPath: ENV_TEMPLATE_PATH,
    installedAt: new Date().toISOString(),
  };
  fs.writeFileSync(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

function npmInstallGlobal(installDir) {
  const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm";
  const result = spawnSync(npmCmd, ["install", "-g", "."], {
    cwd: installDir,
    stdio: "inherit",
  });
  return result.status === 0;
}

function verify(venvPython, installDir) {
  execFileSync(venvPython, [path.join("src", "main.py"), "status"], {
    cwd: installDir,
    stdio: "inherit",
    env: {
      ...process.env,
      INSTAGRAM_AGENT_HOME: installDir,
      INSTAGRAM_AGENT_MANIFEST: MANIFEST_PATH,
    },
  });
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const installDir = path.resolve(args.target);

  console.log("==================================");
  console.log("  Instagram Agent Installer");
  console.log("==================================");
  console.log(`Install directory: ${installDir}`);

  ensureDir(installDir);
  ensureDir(path.join(installDir, "data", "sessions"));
  copyRecursive(REPO_ROOT, installDir);

  const pythonCommand = findPython();
  const venvDir = createVenv(pythonCommand, installDir);
  const venvPython = resolveVenvPython(venvDir);

  if (!args.skipDeps) {
    installDeps(venvPython, installDir);
  }

  writeEnvTemplate(args.username);
  writeManifest(installDir, venvPython);

  let globalWorked = false;
  if (!args.skipGlobal) {
    globalWorked = npmInstallGlobal(installDir);
  }

  verify(venvPython, installDir);

  console.log("");
  console.log("Instagram installed successfully.");
  console.log(`Saved absolute path: ${installDir}`);
  console.log(`Saved manifest: ${MANIFEST_PATH}`);
  console.log(`Saved env template: ${ENV_TEMPLATE_PATH}`);
  if (globalWorked) {
    console.log("Global command available: instagram");
  } else {
    console.log(`Fallback command: node ${path.join(installDir, "install", "launcher.js")} status`);
  }
}

main();
