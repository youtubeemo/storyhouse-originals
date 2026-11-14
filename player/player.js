/*
 * StoryHouse – Film ansehen.
 * Entschlüsselt Dateien von scripts/encrypt_outputs.sh vollständig im Browser:
 *   openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt  (Digest SHA-256, OpenSSL 3)
 *   Format: "Salted__" | 8 Byte Salt | AES-256-CBC (PKCS#7)
 *   Schlüssel+IV = PBKDF2-HMAC-SHA256(Passphrase, Salt, 600000) -> 32 + 16 Byte
 * Kein Netzwerk (CSP connect-src 'none'), keine Speicherung, keine Protokollierung.
 */
(function (root) {
  "use strict";

  var MAGIC = [0x53, 0x61, 0x6c, 0x74, 0x65, 0x64, 0x5f, 0x5f]; // "Salted__"
  var ITERATIONS = 600000;

  async function decryptOpenSSL(data, passphrase, subtle) {
    subtle = subtle || (root.crypto && root.crypto.subtle);
    if (!subtle) throw new Error("Dieser Browser unterstützt keine sichere Entschlüsselung (WebCrypto fehlt).");
    var bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    if (bytes.length < 32 || MAGIC.some(function (b, i) { return bytes[i] !== b; })) {
      throw new Error("Keine StoryHouse-Datei (.enc) – bitte die verschlüsselte Datei aus dem Artefakt wählen.");
    }
    if ((bytes.length - 16) % 16 !== 0) throw new Error("Datei ist unvollständig oder beschädigt.");
    var salt = bytes.slice(8, 16);
    var baseKey = await subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveBits"]);
    var bits = new Uint8Array(await subtle.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt: salt, iterations: ITERATIONS }, baseKey, 384));
    var key = await subtle.importKey("raw", bits.slice(0, 32), { name: "AES-CBC" }, false, ["decrypt"]);
    var iv = bits.slice(32, 48);
    bits.fill(0);
    try {
      return new Uint8Array(await subtle.decrypt({ name: "AES-CBC", iv: iv }, key, bytes.subarray(16)));
    } catch (e) {
      throw new Error("Passphrase falsch oder Datei beschädigt.");
    }
  }

  function plainName(name) {
    var base = String(name || "film.mp4.enc").split(/[\\/]/).pop();
    return base.replace(/\.enc$/i, "") || "film.mp4";
  }

  function looksLikeMp4(bytes) {
    // ISO-BMFF: Bytes 4..7 = "ftyp"
    return bytes.length > 12 && bytes[4] === 0x66 && bytes[5] === 0x74 && bytes[6] === 0x79 && bytes[7] === 0x70;
  }

  root.StoryhousePlayer = { decryptOpenSSL: decryptOpenSSL, plainName: plainName, looksLikeMp4: looksLikeMp4 };

  if (typeof document === "undefined") return;

  var $ = function (id) { return document.getElementById(id); };
  var form = $("form"), status = $("status"), result = $("result");
  var currentUrl = null, currentFile = null;

  function say(text, isError) {
    status.textContent = text;
    status.className = isError ? "error" : "";
  }

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var file = $("file").files[0];
    var pass = $("pass").value;
    if (!file) return say("Bitte zuerst die .enc-Datei wählen.", true);
    $("go").disabled = true;
    result.hidden = true;
    say("Entschlüssele auf diesem Gerät …");
    try {
      var plain = await decryptOpenSSL(await file.arrayBuffer(), pass);
      $("pass").value = "";
      pass = null;
      var name = plainName(file.name);
      if (/\.mp4$/i.test(name) && !looksLikeMp4(plain)) {
        // AES-CBC erkennt eine falsche Passphrase nicht immer zuverlässig -> Inhalt prüfen
        throw new Error("Passphrase falsch oder Datei beschädigt.");
      }
      var type = looksLikeMp4(plain) ? "video/mp4" : "application/octet-stream";
      if (currentUrl) URL.revokeObjectURL(currentUrl);
      currentFile = new File([plain], name, { type: type });
      currentUrl = URL.createObjectURL(currentFile);
      $("download").href = currentUrl;
      $("download").setAttribute("download", name);
      if (type === "video/mp4") {
        $("video").src = currentUrl;
        $("video").hidden = false;
      } else {
        $("video").hidden = true;
      }
      $("share").hidden = !(navigator.canShare && navigator.canShare({ files: [currentFile] }));
      result.hidden = false;
      say("Fertig – " + name + " (" + (plain.length / 1048576).toFixed(1) + " MB). Nur auf diesem Gerät entschlüsselt.");
    } catch (e) {
      say(e && e.message ? e.message : "Entschlüsselung fehlgeschlagen.", true);
    } finally {
      $("go").disabled = false;
    }
  });

  $("share").addEventListener("click", async function () {
    if (!currentFile) return;
    try { await navigator.share({ files: [currentFile], title: currentFile.name }); }
    catch (e) { if (e && e.name !== "AbortError") say("Teilen nicht möglich – bitte „Herunterladen“ nutzen.", true); }
  });
})(typeof window !== "undefined" ? window : globalThis);
