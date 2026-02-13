import fetch from "node-fetch";
import dotenv from "dotenv";
import fs from "fs";
import path from "path";
import chalk from "chalk";
import readline from "readline";

dotenv.config();

const CLIENT_ID = process.env.CLIENT_ID;
const CLIENT_SECRET = process.env.CLIENT_SECRET;
const COUNTRY_CODE = "US";
const BASE_DELAY = 500;

const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

// --- Helpers ---
function truncate(str, len = 40) {
    return str.length > len ? str.substring(0, len) + "..." : str;
}

function extractPlaylistId(url) {
    if (!url) return null;
    const match = url.match(/playlist\/([0-9a-fA-F-]{36})/);
    return match ? match[1] : null;
}

function sanitizeFilename(name) {
    return name.replace(/[<>:"/\\|?*]+/g, "_").trim();
}

function normalizeTidalUrl(link) {
    if (!link) return null;
    let urlObj;
    try {
        urlObj = new URL(link, "https://openapi.tidal.com");
    } catch (e) {
        return null;
    }
    urlObj.protocol = "https:";
    urlObj.host = "openapi.tidal.com";
    if (!urlObj.pathname.startsWith("/v2/")) {
        const cleanPath = urlObj.pathname.startsWith("/") ? urlObj.pathname.substring(1) : urlObj.pathname;
        urlObj.pathname = `/v2/${cleanPath}`;
    }
    if (!urlObj.searchParams.has("countryCode")) urlObj.searchParams.set("countryCode", COUNTRY_CODE);
    if (!urlObj.searchParams.has("include")) urlObj.searchParams.set("include", "items");
    return urlObj.toString();
}

async function getAccessToken() {
    const res = await fetch("https://auth.tidal.com/v1/oauth2/token", {
        method: "POST",
        headers: {
            "Authorization": `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64")}`,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: "grant_type=client_credentials",
    });
    if (!res.ok) throw new Error("Authentication failed.");
    return (await res.json()).access_token;
}

async function fetchWithRetry(url, accessToken, retries = 5) {
    for (let i = 0; i < retries; i++) {
        const res = await fetch(url, {
            headers: {
                "Authorization": `Bearer ${accessToken}`,
                "Accept": "application/vnd.api+json",
            },
        });
        if (res.status === 429) {
            const retryHeader = res.headers.get("retry-after");
            let serverWaitTime = retryHeader ? parseInt(retryHeader, 10) * 1000 : 5000;
            const cutWaitTime = Math.ceil(serverWaitTime / 3);
            process.stdout.write("\n");
            console.log(chalk.yellow(` [!] Rate Limit: Waiting ${cutWaitTime}ms...`));
            await sleep(cutWaitTime);
            continue;
        }
        
        // Return response directly for higher-level error handling (like 400s)
        return res; 
    }
    throw new Error("Timeout");
}

function getUserHome() {
    return process.env.HOME || process.env.USERPROFILE;
}

// --- Input Helpers ---
async function askQuestion(query) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise(resolve => rl.question(query, ans => {
        rl.close();
        resolve(ans);
    }));
}

async function prompt400Error(refId) {
    console.log(chalk.red(`\n [!] HTTP 400 Error for Item ID: ${refId}`));
    const options = ["Retry", "Skip", "Input New URL"];
    let selectedIndex = 0;

    return new Promise((resolve) => {
        const stdin = process.stdin;
        stdin.setRawMode(true);
        stdin.resume();
        stdin.setEncoding("utf-8");

        function render() {
            readline.cursorTo(process.stdout, 0);
            readline.clearScreenDown(process.stdout);
            console.log(chalk.yellow("Choose action:"));
            options.forEach((opt, i) => {
                console.log(i === selectedIndex ? chalk.cyan.bold(`> ${opt}`) : `  ${opt}`);
            });
        }

        render();

        stdin.on("data", async (key) => {
            if (key === "\u001b[A") { // Up
                selectedIndex = Math.max(0, selectedIndex - 1);
                render();
            } else if (key === "\u001b[B") { // Down
                selectedIndex = Math.min(options.length - 1, selectedIndex + 1);
                render();
            } else if (key === "\r") { // Enter
                stdin.setRawMode(false);
                stdin.pause();
                
                if (options[selectedIndex] === "Input New URL") {
                    const newUrl = await askQuestion(chalk.cyan("Enter new TIDAL URL: "));
                    resolve({ action: "url", url: newUrl });
                } else {
                    resolve({ action: options[selectedIndex].toLowerCase() });
                }
            } else if (key === "\u0003") { // Ctrl+C
                process.exit();
            }
        });
    });
}

// --- File System Navigator ---
async function fileNavigator(startDir) {
    let currentDir = startDir;
    let selectedIndex = 0;

    return new Promise((resolve) => {
        const stdin = process.stdin;
        stdin.setRawMode(true);
        stdin.resume();
        stdin.setEncoding("utf-8");

        function render() {
            console.clear();
            console.log(chalk.white.bold("\n TIDAL PLAYLIST EXPORTER - FILE NAVIGATOR"));
            console.log(chalk.dim(" -----------------------------------------"));
            console.log(chalk.cyan(` Directory: ${currentDir}\n`));

            let files = [];
            try {
                files = fs.readdirSync(currentDir, { withFileTypes: true })
                    .filter(f => f.isDirectory() || f.name.endsWith('.txt'))
                    .sort((a, b) => b.isDirectory() - a.isDirectory());
            } catch (e) {
                console.log(chalk.red(" Cannot access directory."));
            }

            const displayItems = [
                { name: "..", isDirectory: true },
                ...files.map(f => ({ name: f.name, isDirectory: f.isDirectory() }))
            ];

            if (selectedIndex >= displayItems.length) selectedIndex = displayItems.length - 1;
            if (selectedIndex < 0) selectedIndex = 0;

            displayItems.forEach((item, index) => {
                const prefix = index === selectedIndex ? chalk.cyan.bold("> ") : "  ";
                const name = item.isDirectory ? chalk.blue(`[${item.name}]`) : chalk.white(item.name);
                console.log(`${prefix}${name}`);
            });
            console.log(chalk.dim("\n (Use Arrows to navigate, Enter to select/open, Backspace to go back)"));
        }

        render();

        stdin.on("data", (key) => {
            let files = fs.readdirSync(currentDir, { withFileTypes: true })
                .filter(f => f.isDirectory() || f.name.endsWith('.txt'))
                .sort((a, b) => b.isDirectory() - a.isDirectory());
            const displayItems = [
                { name: "..", isDirectory: true },
                ...files.map(f => ({ name: f.name, isDirectory: f.isDirectory() }))
            ];

            if (key === "\u001b[A") { // Up
                selectedIndex = Math.max(0, selectedIndex - 1);
            } else if (key === "\u001b[B") { // Down
                selectedIndex = Math.min(displayItems.length - 1, selectedIndex + 1);
            } else if (key === "\r") { // Enter
                const selected = displayItems[selectedIndex];
                const newPath = path.resolve(currentDir, selected.name);
                if (selected.isDirectory) {
                    currentDir = newPath;
                    selectedIndex = 0;
                } else {
                    stdin.setRawMode(false);
                    stdin.pause();
                    resolve(newPath);
                    return;
                }
            } else if (key === "\u007f") { // Backspace
                currentDir = path.dirname(currentDir);
                selectedIndex = 0;
            } else if (key === "\u0003") { // Ctrl+C
                process.exit();
            }
            render();
        });
    });
}

// --- Main Processing Logic ---
async function processPlaylist(playlistId, token, isTopLevel = false) {
    const playlistMetaUrl = normalizeTidalUrl(`/playlists/${playlistId}`);
    const res = await fetchWithRetry(playlistMetaUrl, token);
    
    if (!res || !res.ok) {
        console.log(chalk.red(` [!] Playlist ID ${playlistId} not found or inaccessible (HTTP ${res?.status}).`));
        return;
    }
    const playlistData = await res.json();

    const playlistName = playlistData.data.attributes.name;
    const safeName = sanitizeFilename(playlistName);
    const outputDir = path.join(getUserHome(), "Music/Playlist");
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    const outputPath = path.join(outputDir, `${safeName}.json`);

    console.log(chalk.green(`\n [+] Found: "${playlistName}"`));

    let allTrackRefs = [];
    let nextUrl = normalizeTidalUrl(`/playlists/${playlistId}/relationships/items`);
    console.log(chalk.blue(" [*] Mapping playlist structure..."));

    while (nextUrl) {
        await sleep(BASE_DELAY);
        const res = await fetchWithRetry(nextUrl, token);
        if (!res || !res.ok) break;
        const data = await res.json();
        if (data.data) allTrackRefs.push(...data.data);
        nextUrl = data.links?.next ? normalizeTidalUrl(data.links.next) : null;
    }

    const totalTracks = allTrackRefs.length;
    console.log(chalk.blue(` [*] Processing ${totalTracks} items...\n`));

    const finalTracks = [];
    for (let i = 0; i < allTrackRefs.length; i++) {
        let ref = allTrackRefs[i];
        const trackOrder = i + 1;
        const progressLabel = `[${trackOrder}/${allTrackRefs.length}]`;
        await sleep(BASE_DELAY);

        let success = false;
        while (!success) {
            try {
                const trackUrl = normalizeTidalUrl(`/tracks/${ref.id}?include=artists,albums`);
                const res = await fetchWithRetry(trackUrl, token);

                if (res.status === 400) {
                    const decision = await prompt400Error(ref.id);
                    if (decision.action === "skip") {
                        console.log(chalk.yellow(` ${progressLabel} [SKIPPED] ID: ${ref.id}`));
                        finalTracks.push({ order: trackOrder, id: ref.id, status: "skipped" });
                        success = true;
                    } else if (decision.action === "retry") {
                        continue; // Loop again
                    } else if (decision.action === "url") {
                        const newId = extractPlaylistId(decision.url);
                        if (newId) {
                            ref.id = newId; // Update ID and retry
                            continue;
                        } else {
                            console.log(chalk.red(" Invalid URL provided."));
                            continue;
                        }
                    }
                } else if (res.ok) {
                    const data = await res.json();
                    if (data && data.data) {
                        const track = data.data;
                        const included = data.included || [];
                        const rawArtists = track.relationships.artists.data.map(
                            (r) => included.find((x) => x.type === "artists" && x.id === r.id)?.attributes.name || "Unknown"
                        );
                        const artistsStr = rawArtists.join(", ");
                        const album = included.find((x) => x.type === "albums")?.attributes.title || "Unknown Album";

                        finalTracks.push({
                            order: trackOrder,
                            title: track.attributes.title,
                            artists: rawArtists,
                            album,
                            id: ref.id,
                            isrc: track.attributes.isrc,
                        });
                        console.log(` ${chalk.dim(progressLabel)} Processing "${truncate(track.attributes.title, 30)}"`);
                        success = true;
                    }
                } else {
                    throw new Error(`HTTP ${res.status}`);
                }
            } catch (e) {
                console.log(chalk.red(` ${progressLabel} [ERROR] ID ${ref.id}: ${e.message}`));
                finalTracks.push({ order: trackOrder, id: ref.id, status: "error" });
                success = true;
            }
        }

        if ((i + 1) % 5 === 0 || i === allTrackRefs.length - 1) {
            fs.writeFileSync(outputPath, JSON.stringify({ playlist: playlistName, tracks: finalTracks }, null, 2));
        }
    }
    console.log(chalk.bold.green(`\n [SUCCESS] JSON Saved to ${outputPath}\n`));
}

// --- Menu UI ---
async function startApp() {
    console.clear();
    console.log(chalk.white.bold("\n TIDAL PLAYLIST EXPORTER"));
    console.log(chalk.dim(" -----------------------\n"));

    if (!CLIENT_ID || !CLIENT_SECRET) {
        console.log(chalk.red(" [ERROR] Missing credentials in .env"));
        process.exit(1);
    }

    const modes = ["Single URL", "Text File (Multiple URLs)"];
    let selectedModeIndex = 0;

    const getMenu = () => {
        return modes.map((mode, i) => {
            return i === selectedModeIndex ? chalk.cyan.bold(`> ${mode}`) : `  ${mode}`;
        }).join("\n");
    };

    const modeChoice = await new Promise((resolve) => {
        const stdin = process.stdin;
        stdin.setRawMode(true);
        stdin.resume();
        stdin.setEncoding("utf-8");

        console.log("Choose export mode:");
        console.log(getMenu());

        stdin.on("data", (key) => {
            if (key === "\u001b[A") { // Up
                selectedModeIndex = 0;
            } else if (key === "\u001b[B") { // Down
                selectedModeIndex = 1;
            } else if (key === "\r") { // Enter
                stdin.setRawMode(false);
                stdin.pause();
                resolve(modes[selectedModeIndex]);
                return;
            } else if (key === "\u0003") { // Ctrl+C
                process.exit();
            }
            
            readline.cursorTo(process.stdout, 0);
            readline.moveCursor(process.stdout, 0, -3);
            readline.clearScreenDown(process.stdout);
            console.log("Choose export mode:");
            console.log(getMenu());
        });
    });

    let playlistIds = [];

    if (modeChoice === "Single URL") {
        const url = await askQuestion(chalk.yellow("Enter TIDAL Playlist URL: "));
        const id = extractPlaylistId(url);
        if (id) playlistIds.push(id);
    } else {
        const filePath = await fileNavigator(process.cwd());
        const fileContent = fs.readFileSync(filePath, "utf-8");
        playlistIds = fileContent
            .split(/\r?\n/)
            .map((line) => extractPlaylistId(line.trim()))
            .filter((id) => id !== null);
        console.log(chalk.blue(` [*] Found ${playlistIds.length} valid URLs in file.`));
    }

    if (playlistIds.length === 0) {
        console.log(chalk.red(" [ERROR] No valid playlists found."));
        return;
    }

    try {
        console.log(chalk.blue("\n [*] Authenticating..."));
        const token = await getAccessToken();
        for (const playlistId of playlistIds) {
            await processPlaylist(playlistId, token);
        }
    } catch (err) {
        console.error(chalk.red(`\n [CRITICAL] ${err.message}`));
    }
}

startApp();
