Install the Instagram agent from GitHub and make sure it is callable correctly for this machine.

Repository:
https://github.com/josephtandle/instagram-agent

Do this carefully:
1. Detect whether I am on macOS, Windows, or Linux before choosing commands.
2. Resolve my home directory dynamically. Do not hard-code a username.
3. Install the repo into a Tools/Instagram folder inside my home directory.
4. Use shell-safe commands on macOS/Linux and PowerShell-safe commands on Windows.
5. If the folder already exists, pull the latest main branch. Otherwise clone the repo.
6. Run the bundled installer from inside the repo:
   - macOS/Linux: node install/install-instagram.js --target <resolved-home>/Tools/Instagram
   - Windows: node install/install-instagram.js --target <resolved-home>\Tools\Instagram
7. Let the installer create the Python virtualenv, install the Python requirements, and save the resolved absolute install path.
8. Make sure the saved install manifest exists at:
   - macOS/Linux: ~/.instagram-agent/install.json
   - Windows: $env:USERPROFILE\.instagram-agent\install.json
9. Try to make the global command available by running npm install -g . from inside the installed repo if the installer did not already do it.
10. Verify the command works:
   - preferred: instagram status
   - fallback: node <resolved-home>/Tools/Instagram/install/launcher.js status
11. Show me:
   - where the repo was installed
   - where the absolute path was saved
   - whether the global instagram command works
   - the exact fallback command if global install did not work

Important:
- This tool is mainly for reading DMs, reading comments, researching profiles, and supporting CRM capture.
- Do not frame it as a mass-DM tool.
- If anything requires manual approval or login, stop and tell me exactly what needs to happen next.
