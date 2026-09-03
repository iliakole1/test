/* Water model and usage parsing, shared by the web app and the extension.
 *
 * Mirrors water_model.py and usage_reader.py; both read the same constants.json,
 * so the two implementations cannot drift on the numbers.
 */
(function (global) {
  "use strict";

  /* ---------- volume formatting ---------- */

  function formatVolume(ml) {
    if (!isFinite(ml)) return "0 mL";
    if (ml < 1) return ml.toFixed(2) + " mL";
    if (ml < 1000) return ml.toFixed(1) + " mL";
    if (ml < 1e6) return (ml / 1000).toFixed(2) + " L";
    return (ml / 1e6).toFixed(2) + " m³";
  }

  function formatInt(n) {
    return Math.round(n).toLocaleString("en-US");
  }

  /** Largest everyday volume the total covers at least once. */
  function nearestComparison(ml, comparisons) {
    if (ml <= 0) return { label: comparisons[0].label, count: 0 };
    for (var i = comparisons.length - 1; i >= 0; i--) {
      if (ml >= comparisons[i].ml) {
        return { label: comparisons[i].label, count: ml / comparisons[i].ml };
      }
    }
    return { label: comparisons[0].label, count: ml / comparisons[0].ml };
  }

  /** Smallest everyday volume larger than the total, for the dial to fill. */
  function fillReference(ml, comparisons) {
    for (var i = 0; i < comparisons.length; i++) {
      if (ml < comparisons[i].ml) return comparisons[i];
    }
    return comparisons[comparisons.length - 1];
  }

  /* ---------- energy ---------- */

  function tokenEnergyWh(tokens, rates) {
    return (
      (tokens.output || 0) * rates.output +
      (tokens.input || 0) * rates.input +
      (tokens.cache_write || 0) * rates.cache_write +
      (tokens.cache_read || 0) * rates.cache_read
    );
  }

  function emptyTokens() {
    return { input: 0, output: 0, cache_write: 0, cache_read: 0 };
  }

  function addTokens(a, b) {
    return {
      input: a.input + (b.input || 0),
      output: a.output + (b.output || 0),
      cache_write: a.cache_write + (b.cache_write || 0),
      cache_read: a.cache_read + (b.cache_read || 0),
    };
  }

  function totalTokens(t) {
    return t.input + t.output + t.cache_write + t.cache_read;
  }

  /* ---------- Claude Code transcripts ---------- */

  /* Claude Code appends one JSON object per line and writes each API response
   * several times as it streams, every copy repeating the same cumulative usage
   * block. Deduplicating on (message id, request id) is the difference between a
   * correct total and one three or four times too high. `seen` is passed in so a
   * dedup set can span every file in one import. */
  function parseTranscript(text, seen, out) {
    var lines = text.split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      var entry;
      try {
        entry = JSON.parse(line);
      } catch (e) {
        continue; // a half-written line from a live session
      }
      if (!entry || entry.type !== "assistant") continue;
      var message = entry.message;
      if (!message || typeof message !== "object") continue;
      var usage = message.usage;
      if (!usage || typeof usage !== "object") continue;

      var key = (message.id || entry.uuid || "") + "|" + (entry.requestId || "");
      if (seen.has(key)) continue;
      seen.add(key);

      var stamp = entry.timestamp;
      if (!stamp) continue;
      var date = String(stamp).slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;

      out.calls += 1;
      var t = {
        input: usage.input_tokens || 0,
        output: usage.output_tokens || 0,
        cache_write: usage.cache_creation_input_tokens || 0,
        cache_read: usage.cache_read_input_tokens || 0,
      };
      out.tokens = addTokens(out.tokens, t);
      out.days[date] = addTokens(out.days[date] || emptyTokens(), t);
    }
    return out;
  }

  /* ---------- claude.ai conversation export ---------- */

  /* The account data export carries message text but no token counts, so tokens
   * are estimated from text length at ~4 characters per token. Assistant text is
   * counted as output; human text as input. Prompt-cache effects are invisible
   * here, so a chat total is rougher than a Claude Code one. */
  function parseConversations(json, out) {
    var conversations = Array.isArray(json) ? json : json.conversations;
    if (!Array.isArray(conversations)) return out;

    for (var i = 0; i < conversations.length; i++) {
      var convo = conversations[i];
      var messages = convo && (convo.chat_messages || convo.messages);
      if (!Array.isArray(messages)) continue;

      for (var j = 0; j < messages.length; j++) {
        var msg = messages[j];
        if (!msg) continue;
        var text = extractText(msg);
        if (!text) continue;

        var stamp = msg.created_at || msg.updated_at || convo.created_at;
        if (!stamp) continue;
        var date = String(stamp).slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;

        var estimated = Math.ceil(text.length / 4);
        var sender = msg.sender || msg.role;
        var t = emptyTokens();
        if (sender === "assistant") {
          t.output = estimated;
          out.calls += 1;
        } else {
          t.input = estimated;
        }
        out.tokens = addTokens(out.tokens, t);
        out.days[date] = addTokens(out.days[date] || emptyTokens(), t);
      }
    }
    return out;
  }

  /* Export format has shifted over time: older files put the text on `text`,
   * newer ones spread it across a `content` array of typed blocks. */
  function extractText(msg) {
    if (typeof msg.text === "string" && msg.text) return msg.text;
    if (Array.isArray(msg.content)) {
      var parts = [];
      for (var i = 0; i < msg.content.length; i++) {
        var block = msg.content[i];
        if (block && typeof block.text === "string") parts.push(block.text);
      }
      return parts.join(" ");
    }
    return "";
  }


  /* ---------- ZIP reading ---------- */

  /* The claude.ai data export arrives as ZIPs, so the app reads them directly
   * rather than making you unzip first. This is a minimal reader for the one
   * case that matters: stored and deflated entries in a non-ZIP64 archive. It
   * uses DecompressionStream rather than pulling in a library, which keeps the
   * app dependency-free and working offline.
   *
   * Sizes are taken from the central directory, not the local headers, because
   * an archive written as a stream leaves the local sizes zeroed. */

  var SIG_EOCD = 0x06054b50;
  var SIG_CENTRAL = 0x02014b50;

  function findEndOfCentralDirectory(view) {
    // The record is at the end, after a comment of up to 65535 bytes.
    var min = Math.max(0, view.byteLength - 22 - 65535);
    for (var i = view.byteLength - 22; i >= min; i--) {
      if (view.getUint32(i, true) === SIG_EOCD) return i;
    }
    return -1;
  }

  async function inflateRaw(bytes) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("This browser cannot unzip files. Unzip it yourself and drop the .json.");
    }
    var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Response(stream).arrayBuffer();
  }

  /** Read a ZIP into [{name, text}], skipping directories and unreadable entries. */
  async function readZip(buffer) {
    var view = new DataView(buffer);
    var eocd = findEndOfCentralDirectory(view);
    if (eocd < 0) throw new Error("That does not look like a ZIP file.");

    var count = view.getUint16(eocd + 10, true);
    var offset = view.getUint32(eocd + 16, true);
    if (offset === 0xffffffff) throw new Error("ZIP64 archives are not supported. Unzip it yourself and drop the .json.");

    var decoder = new TextDecoder("utf-8");
    var entries = [];

    for (var i = 0; i < count && offset + 46 <= buffer.byteLength; i++) {
      if (view.getUint32(offset, true) !== SIG_CENTRAL) break;
      var method = view.getUint16(offset + 10, true);
      var compressedSize = view.getUint32(offset + 20, true);
      var nameLength = view.getUint16(offset + 28, true);
      var extraLength = view.getUint16(offset + 30, true);
      var commentLength = view.getUint16(offset + 32, true);
      var localOffset = view.getUint32(offset + 42, true);
      var name = decoder.decode(new Uint8Array(buffer, offset + 46, nameLength));
      offset += 46 + nameLength + extraLength + commentLength;

      if (name.endsWith("/")) continue; // a directory entry holds no data

      // The local header repeats the name and extra field at its own lengths.
      var localNameLength = view.getUint16(localOffset + 26, true);
      var localExtraLength = view.getUint16(localOffset + 28, true);
      var dataStart = localOffset + 30 + localNameLength + localExtraLength;
      var raw = new Uint8Array(buffer, dataStart, compressedSize);

      try {
        var bytes = method === 0 ? raw : new Uint8Array(await inflateRaw(raw));
        entries.push({ name: name, text: decoder.decode(bytes) });
      } catch (e) {
        // One unreadable member should not lose the rest of the archive.
      }
    }
    return entries;
  }

  function emptyImport() {
    return { calls: 0, tokens: emptyTokens(), days: {} };
  }

  global.WaterModel = {
    formatVolume: formatVolume,
    formatInt: formatInt,
    nearestComparison: nearestComparison,
    fillReference: fillReference,
    tokenEnergyWh: tokenEnergyWh,
    emptyTokens: emptyTokens,
    addTokens: addTokens,
    totalTokens: totalTokens,
    emptyImport: emptyImport,
    parseTranscript: parseTranscript,
    parseConversations: parseConversations,
    readZip: readZip,
  };
})(typeof window !== "undefined" ? window : globalThis);
