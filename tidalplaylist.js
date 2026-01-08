import fetch from "node-fetch";
import dotenv from "dotenv";
import fs from "fs";
import path from "path";
import inquirer from "inquirer";
import chalk from "chalk";
import readline from "readline";

dotenv.config();

const CLIENT_ID = process.env.CLIENT_ID;
const CLIENT_SECRET = process.env.CLIENT_SECRET;
const COUNTRY_CODE = "US";
const BASE_DELAY = 500;

const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

// Limits string length for terminal display to prevent line wrapping
function truncate(str, len = 40) {
  return str.length > len ? str.substring(0, len) + "..." : str;
}

function extractPlaylistId(url) {
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
    const cleanPath = urlObj.pathname.startsWith("/")
      ? urlObj.pathname.substring(1)
      : urlObj.pathname;
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
      let serverWaitTime = 5000;
      if (retryHeader) serverWaitTime = parseInt(retryHeader, 10) * 1000;
      const cutWaitTime = Math.ceil(serverWaitTime / 3);
      process.stdout.write("\n");
      console.log(chalk.yellow(` [!] Rate Limit: Waiting ${cutWaitTime}ms...`));
      await sleep(cutWaitTime);
      continue;
    }

    if (!res.ok) {
      if (res.status === 404) return null;
      throw new Error(`HTTP ${res.status}`);
    }

    return await res.json();
  }

  throw new Error("Timeout");
}

function renderProgressBar(current, total) {
  const width = 30;
  const percent = Math.floor((current / total) * 100);
  const progress = Math.floor((current / total) * width);
  const bar = chalk.cyan("█").repeat(progress) + chalk.dim("░").repeat(width - progress);
  return ` ${bar} ${chalk.bold(percent)}%`;
}

function getUserHome() {
    return process.env.HOME || process.env.USERPROFILE;
}

async function startApp() {
  console.clear();
  console.log(chalk.white.bold("\n TIDAL PLAYLIST EXPORTER"));
  console.log(chalk.dim(" -----------------------\n"));

  if (!CLIENT_ID || !CLIENT_SECRET) {
    console.log(chalk.red(" [ERROR] Missing credentials in .env"));
    process.exit(1);
  }

  const answers = await inquirer.prompt([
    {
      type: "input",
      name: "url",
      message: "Enter TIDAL Playlist URL:",
      validate: (input) => (extractPlaylistId(input) ? true : "Invalid URL"),
    },
  ]);

  try {
    const playlistId = extractPlaylistId(answers.url);
    console.log(chalk.blue("\n [*] Authenticating..."));
    const token = await getAccessToken();

    const playlistMetaUrl = normalizeTidalUrl(`/playlists/${playlistId}`);
    const playlistData = await fetchWithRetry(playlistMetaUrl, token);

    const playlistName = playlistData.data.attributes.name;
    const safeName = sanitizeFilename(playlistName);
    const outputDir = path.join(path.join(getUserHome(), "Music/Playlist"), safeName);
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    const outputPath = path.join(outputDir, `${safeName}.json`);

    console.log(chalk.green(` [+] Found: "${playlistName}"`));

    let allTrackRefs = [];
    let nextUrl = normalizeTidalUrl(`/playlists/${playlistId}/relationships/items`);
    console.log(chalk.blue(" [*] Mapping playlist structure..."));

    while (nextUrl) {
      await sleep(BASE_DELAY);
      const res = await fetchWithRetry(nextUrl, token);
      if (!res) break;
      if (res.data) allTrackRefs.push(...res.data);
      nextUrl = res.links?.next ? normalizeTidalUrl(res.links.next) : null;
    }

    const totalTracks = allTrackRefs.length;
    console.log(chalk.blue(` [*] Processing ${totalTracks} items...\n`));

    const finalTracks = [];
    for (let i = 0; i < totalTracks; i++) {
      const ref = allTrackRefs[i];
      const trackOrder = i + 1;
      const progressLabel = `[${trackOrder}/${totalTracks}]`;
      await sleep(BASE_DELAY);

      try {
        const trackUrl = normalizeTidalUrl(`/tracks/${ref.id}?include=artists,albums`);
        const data = await fetchWithRetry(trackUrl, token);

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

          const displayTitle = truncate(track.attributes.title, 30);
          const displayArtists = truncate(artistsStr, 25);
          const statusLine = ` ${chalk.dim(progressLabel)} Processing "${displayTitle}" - ${displayArtists}`;

          readline.clearLine(process.stdout, 0);
          readline.cursorTo(process.stdout, 0);
          process.stdout.write(statusLine + "\n");
          readline.clearLine(process.stdout, 0);
          readline.cursorTo(process.stdout, 0);
          process.stdout.write(renderProgressBar(trackOrder, totalTracks));
          readline.moveCursor(process.stdout, 0, -1);
          readline.cursorTo(process.stdout, statusLine.length);

        } else {
          readline.clearLine(process.stdout, 0);
          readline.cursorTo(process.stdout, 0);
          console.log(chalk.red(` ${progressLabel} [UNAVAILABLE] ID: ${ref.id}`));
          finalTracks.push({
            order: trackOrder,
            title: "Unavailable Track",
            artists: ["Unknown"],
            album: "Unknown",
            id: ref.id,
            isrc: "N/A",
            status: "unavailable",
          });
        }
      } catch (e) {
        readline.clearLine(process.stdout, 0);
        readline.cursorTo(process.stdout, 0);
        console.log(chalk.red(` ${progressLabel} [ERROR] ID ${ref.id}: ${e.message}`));
        finalTracks.push({
          order: trackOrder,
          title: "Error Fetching Track",
          artists: ["Unknown"],
          album: "Unknown",
          id: ref.id,
          isrc: "N/A",
          status: "error",
        });
      }

      if ((i + 1) % 5 === 0 || i === totalTracks - 1) {
        fs.writeFileSync(outputPath, JSON.stringify({ playlist: playlistName, tracks: finalTracks }, null, 2));
      }
    }

    readline.moveCursor(process.stdout, 0, 2);
    console.log(chalk.bold.green(`\n [SUCCESS] JSON Saved to ${outputPath}\n`));

  } catch (err) {
    console.error(chalk.red(`\n [CRITICAL] ${err.message}`));
  }
}

startApp();
