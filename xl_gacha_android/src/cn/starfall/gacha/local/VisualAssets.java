package cn.starfall.gacha.local;

import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.nio.file.*;
import java.util.*;
import javax.net.ssl.HttpsURLConnection;

/** Credential-free, raster-only visual updates. A catalog pins the mapping hash. */
public final class VisualAssets {
    static final int MAX_MAPPING = 1024 * 1024, MAX_IMAGE = 4 * 1024 * 1024;
    private static final long MAX_TOTAL = 32 * 1024 * 1024L, BACKOFF = 60000L;
    interface Transport { byte[] get(String url, int limit) throws Exception; }
    private final File directory;
    private final JSONObject bundled;
    private final Transport transport;
    private Snapshot current, previous;
    private JSONObject requested;
    private String source = "", context = "", phase = "unconfigured", error = "";
    private long generation, checkedAt, retryAt;
    private boolean busy, pending, pendingForce;

    private static final class Snapshot {
        JSONObject descriptor, mapping, effective;
        String revision;
        File directory;
    }

    public VisualAssets(File cacheDirectory, JSONObject bundledManifest) throws Exception {
        this(cacheDirectory, bundledManifest, VisualAssets::download);
    }
    VisualAssets(File cacheDirectory, JSONObject bundledManifest, Transport transport) throws Exception {
        directory = cacheDirectory; bundled = copy(bundledManifest); this.transport = transport;
        require(directory.isDirectory() || directory.mkdirs(), "VISUAL_CACHE_UNAVAILABLE");
        try {
            JSONObject state = parse(read(new File(directory, "active.json"), 16384));
            current = restore(state.optJSONObject("current"));
            previous = restore(state.optJSONObject("previous"));
            if (current == null) { current = previous; previous = null; }
        } catch (Exception ignored) { }
        if (current == null) {
            try { current = restore(parse(read(new File(directory, "previous.json"), 16384))); }
            catch (Exception ignored) { }
        }
        cleanup();
    }

    public synchronized JSONObject manifest() {
        try {
            return copy(current == null ? bundled : current.effective).put("_revision", current == null ? "bundled" : current.revision);
        } catch (JSONException e) { throw new IllegalStateException(e); }
    }
    public synchronized JSONObject status() {
        try {
            return new JSONObject().put("phase", phase).put("revision", current == null ? "bundled" : current.revision)
                .put("version", current == null ? "" : current.descriptor.getString("version"))
                .put("configured", requested != null && !source.isEmpty()).put("checking", busy).put("error", error)
                .put("checked_at", checkedAt).put("retry_at", retryAt).put("origin", current == null ? "bundled" : "cache");
        } catch (JSONException e) { throw new IllegalStateException(e); }
    }

    /** Called on source changes as well as catalog completion; invalidates an older worker immediately. */
    private synchronized void configure(JSONObject catalog, String catalogSource) {
        JSONObject descriptor = catalog.optJSONObject("visual_assets");
        String next = (catalogSource == null ? "" : catalogSource) + "\n" + (descriptor == null ? "" : descriptor.toString());
        if (!next.equals(context)) {
            context = next; generation++; retryAt = 0; error = "";
            source = catalogSource == null ? "" : catalogSource;
            try { requested = descriptor == null ? null : copy(descriptor); }
            catch (Exception e) { requested = null; }
        }
    }
    public void start(JSONObject catalog, String catalogSource, boolean force) {
        synchronized (this) {
            configure(catalog, catalogSource);
            if (busy) { pending = true; pendingForce |= force; return; }
            busy = true; phase = "checking";
        }
        Thread worker = new Thread(() -> runCheck(force), "visual-assets-update");
        worker.setDaemon(true); worker.start();
    }
    public JSONObject check(JSONObject catalog, String catalogSource, boolean force) {
        synchronized (this) {
            configure(catalog, catalogSource);
            if (busy) { pending = true; pendingForce |= force; return result(false); }
            busy = true;
        }
        return runCheck(force);
    }
    private JSONObject runCheck(boolean force) {
        JSONObject descriptor;
        String requestedSource;
        long token;
        synchronized (this) {
            descriptor = requested; requestedSource = source; token = generation;
            if (descriptor == null || requestedSource.isEmpty()) { phase = "unconfigured"; busy = false; return result(false); }
            if (!force && System.currentTimeMillis() < retryAt) { phase = error.isEmpty() ? "current" : "error"; busy = false; return result(false); }
            phase = "checking"; error = "";
        }
        boolean updated = false;
        File staging = null;
        try {
            CatalogUpdates.validateSource(requestedSource);
            validateDescriptor(descriptor);
            String revision = descriptor.getString("sha256");
            synchronized (this) {
                if (current != null) {
                    int order = compare(descriptor.getString("version"), current.descriptor.getString("version"));
                    require(order >= 0, "VISUAL_DOWNGRADE_REJECTED");
                    if (order == 0) {
                        require(current.revision.equals(revision), "VISUAL_VERSION_CONFLICT");
                        require(token == generation, "VISUAL_SOURCE_CHANGED");
                        if (!force || restore(current.descriptor) != null) { success(); return result(false); }
                    }
                }
            }
            URI base = new URI(requestedSource);
            byte[] raw = transport.get(base.resolve(descriptor.getString("file")).toString(), MAX_MAPPING);
            verify(raw, descriptor, MAX_MAPPING);
            JSONObject mapping = parse(raw);
            JSONObject effective = validateMapping(mapping, descriptor);
            staging = new File(directory, revision);
            require(staging.isDirectory() || staging.mkdir(), "VISUAL_CACHE_UNAVAILABLE");
            JSONObject files = mapping.getJSONObject("files");
            Iterator<String> names = files.keys();
            while (names.hasNext()) {
                String path = names.next(); JSONObject file = files.getJSONObject(path);
                synchronized (this) { require(token == generation, "VISUAL_SOURCE_CHANGED"); }
                byte[] image = transport.get(base.resolve(path).toString(), file.getInt("size"));
                verify(image, file, MAX_IMAGE); validateImage(path, image);
                atomic(new File(staging, file.getString("sha256")), image);
            }
            atomic(new File(staging, "mapping.json"), raw);
            Snapshot candidate = new Snapshot(); candidate.descriptor = copy(descriptor); candidate.mapping = mapping;
            candidate.effective = effective; candidate.revision = revision; candidate.directory = staging;
            synchronized (this) {
                require(token == generation, "VISUAL_SOURCE_CHANGED");
                Snapshot retained = current != null && current.revision.equals(revision) ? previous : current;
                JSONObject state = new JSONObject().put("current", candidate.descriptor).put("previous", retained == null ? JSONObject.NULL : retained.descriptor);
                if (retained != null) atomic(new File(directory, "previous.json"), retained.descriptor.toString().getBytes(StandardCharsets.UTF_8));
                atomic(new File(directory, "active.json"), state.toString().getBytes(StandardCharsets.UTF_8));
                previous = retained; current = candidate; updated = true; success();
            }
        } catch (Exception e) {
            synchronized (this) {
                if (token == generation) {
                    phase = "error"; String code = e.getMessage();
                    error = code != null && code.matches("VISUAL_[A-Z_]+") ? code : "VISUAL_UPDATE_FAILED";
                    retryAt = System.currentTimeMillis() + BACKOFF;
                }
            }
        } finally {
            synchronized (this) {
                busy = false; cleanup();
                if (pending) {
                    final boolean nextForce = pendingForce; pending = false; pendingForce = false; busy = true;
                    Thread worker = new Thread(() -> runCheck(nextForce), "visual-assets-update"); worker.setDaemon(true); worker.start();
                }
            }
        }
        synchronized (this) { return result(updated); }
    }
    private void success() { checkedAt = System.currentTimeMillis(); retryAt = 0; phase = "current"; error = ""; }
    private JSONObject result(boolean updated) { try { return status().put("updated", updated); } catch (JSONException e) { throw new IllegalStateException(e); } }

    /** Only serves indexed content by immutable revision. Never opens arbitrary relative filenames. */
    public synchronized InputStream open(String path) throws IOException {
        for (Snapshot snapshot : new Snapshot[]{current, previous}) {
            if (snapshot == null) continue;
            String prefix = "/assets/remote/" + snapshot.revision + "/";
            if (path == null || !path.startsWith(prefix)) continue;
            try {
                String key = path.substring(prefix.length()); validatePath(key);
                JSONObject file = snapshot.mapping.getJSONObject("files").getJSONObject(key);
                byte[] raw = read(new File(snapshot.directory, file.getString("sha256")), MAX_IMAGE);
                verify(raw, file, MAX_IMAGE); validateImage(key, raw);
                return new ByteArrayInputStream(raw);
            } catch (Exception e) { return null; }
        }
        return null;
    }
    private Snapshot restore(JSONObject descriptor) {
        try {
            validateDescriptor(descriptor);
            Snapshot s = new Snapshot(); s.descriptor = copy(descriptor); s.revision = descriptor.getString("sha256");
            s.directory = new File(directory, s.revision);
            byte[] raw = read(new File(s.directory, "mapping.json"), MAX_MAPPING); verify(raw, descriptor, MAX_MAPPING);
            s.mapping = parse(raw); s.effective = validateMapping(s.mapping, descriptor);
            JSONObject files = s.mapping.getJSONObject("files"); Iterator<String> keys = files.keys();
            while (keys.hasNext()) {
                String path = keys.next(); JSONObject file = files.getJSONObject(path);
                byte[] image = read(new File(s.directory, file.getString("sha256")), MAX_IMAGE); verify(image, file, MAX_IMAGE); validateImage(path, image);
            }
            return s;
        } catch (Exception e) { return null; }
    }
    private static void validateDescriptor(JSONObject d) throws Exception {
        require(d != null, "VISUAL_INVALID"); integer(d.opt("schema_version"), 1, 1); version(d.opt("version"));
        require(("visual-assets-" + d.getString("version") + ".json").equals(d.opt("file")), "VISUAL_INVALID");
        hashAndSize(d, MAX_MAPPING);
    }
    private JSONObject validateMapping(JSONObject m, JSONObject d) throws Exception {
        require("starfall-gacha-visual-assets".equals(m.opt("format")), "VISUAL_SCHEMA_UNSUPPORTED");
        integer(m.opt("schema_version"), 1, 1);
        require(d.getString("version").equals(m.opt("version")), "VISUAL_VERSION_MISMATCH");
        // Unknown executable/UI metadata cannot override bundled brand, mascot or application code.
        for (Iterator<String> it = m.keys(); it.hasNext();) require(Arrays.asList("format", "schema_version", "version", "files", "characters", "fashions", "account_items").contains(it.next()), "VISUAL_INVALID");
        JSONObject files = m.getJSONObject("files"); require(files.length() > 0 && files.length() <= 2048, "VISUAL_TOO_LARGE");
        long total = 0; Iterator<String> paths = files.keys();
        while (paths.hasNext()) {
            String path = paths.next(); validatePath(path); JSONObject file = files.getJSONObject(path); hashAndSize(file, MAX_IMAGE);
            total += file.getLong("size"); require(total <= MAX_TOTAL, "VISUAL_TOO_LARGE");
        }
        JSONObject out = copy(bundled);
        JSONObject indexed = out.optJSONObject("files"); if (indexed == null) { indexed = new JSONObject(); out.put("files", indexed); }
        for (Iterator<String> it = files.keys(); it.hasNext();) { String path = it.next(); indexed.put("assets/remote/" + d.getString("sha256") + "/" + path, files.getJSONObject(path).getString("sha256")); }
        Set<String> referenced = new HashSet<>();
        for (String group : new String[]{"characters", "fashions", "account_items"}) {
            JSONObject items = m.optJSONObject(group); if (items == null) continue;
            require(items.length() <= 20000, "VISUAL_TOO_LARGE");
            JSONObject target = out.optJSONObject(group); if (target == null) { target = new JSONObject(); out.put(group, target); }
            Iterator<String> ids = items.keys();
            while (ids.hasNext()) {
                String id = ids.next(); require(id.matches("[1-9][0-9]{0,15}") && Long.parseLong(id) <= 9007199254740991L, "VISUAL_INVALID");
                JSONObject entry = items.getJSONObject(id), clean = new JSONObject();
                for (String key : new String[]{"path", "rect_path"}) {
                    if (!entry.has(key)) continue;
                    require(!key.equals("rect_path") || group.equals("fashions"), "VISUAL_INVALID");
                    Object raw = entry.get(key); require(raw instanceof String, "VISUAL_INVALID"); String path = (String)raw; validatePath(path);
                    if (files.has(path)) { clean.put(key, "assets/remote/" + d.getString("sha256") + "/" + path); referenced.add(path); }
                    else { require(bundled.optJSONObject("files") != null && bundled.getJSONObject("files").has(path), "VISUAL_UNDECLARED_FILE"); clean.put(key, path); }
                }
                require(clean.has(group.equals("fashions") ? "rect_path" : "path"), "VISUAL_INVALID");
                if (group.equals("characters")) {
                    if (entry.has("name")) {
                        Object name = entry.get("name"); require(name instanceof String && ((String)name).length() <= 120 && !((String)name).trim().isEmpty() && !((String)name).matches(".*[\\p{Cntrl}].*"), "VISUAL_INVALID"); clean.put("name", name);
                    }
                    if (entry.has("rarity") && !entry.isNull("rarity")) clean.put("rarity", integer(entry.opt("rarity"), 1, 5));
                }
                if (group.equals("account_items")) {
                    long type = integer(entry.opt("type"), 108, 109); clean.put("type", type);
                    if (entry.has("effect_value") && !entry.isNull("effect_value")) {
                        Object effect = entry.opt("effect_value"); require(effect instanceof String && ((String)effect).matches("[0-9]{1,10}"), "VISUAL_INVALID"); clean.put("effect_value", effect);
                    }
                }
                if (entry.has("source_size") || entry.has("crop_rect")) {
                    require(!group.equals("account_items") || clean.getInt("type") == 108, "VISUAL_INVALID");
                    JSONArray size = entry.getJSONArray("source_size"); require(size.length() == 2, "VISUAL_INVALID");
                    long width = integer(size.get(0), 1, 4096), height = integer(size.get(1), 1, 4096);
                    clean.put("source_size", new JSONArray(size.toString()));
                    if (entry.has("crop_rect")) {
                        JSONArray rect = entry.getJSONArray("crop_rect"); require(rect.length() == 4, "VISUAL_INVALID");
                        long x = integer(rect.get(0), 0, width), y = integer(rect.get(1), 0, height);
                        long w = integer(rect.get(2), 1, width), h = integer(rect.get(3), 1, height);
                        require(x + w <= width && y + h <= height, "VISUAL_INVALID");
                        clean.put("crop_rect", new JSONArray(rect.toString()));
                    }
                }
                target.put(id, clean);
            }
        }
        require(referenced.size() == files.length(), "VISUAL_UNREFERENCED_FILE");
        return out;
    }
    private static void validatePath(String path) throws IOException {
        require(path.length() <= 200 && path.matches("assets/(?:portraits|account|account-rect)/[A-Za-z0-9_-]+\\.(?:webp|png|jpg|jpeg)"), "VISUAL_PATH_INVALID");
    }
    private static void hashAndSize(JSONObject f, int max) throws Exception {
        require(f.opt("sha256") instanceof String && f.getString("sha256").matches("[a-f0-9]{64}"), "VISUAL_INVALID"); integer(f.opt("size"), 1, max);
    }
    private static void version(Object value) throws IOException { require(value instanceof String && ((String)value).matches("[1-9][0-9]{0,19}"), "VISUAL_INVALID"); }
    private static int compare(String a, String b) { return a.length() == b.length() ? a.compareTo(b) : Integer.compare(a.length(), b.length()); }
    private static long integer(Object value, long min, long max) throws IOException { require(value instanceof Integer || value instanceof Long, "VISUAL_INVALID"); long n = ((Number)value).longValue(); require(n >= min && n <= max, "VISUAL_INVALID"); return n; }
    private static void verify(byte[] raw, JSONObject meta, int max) throws Exception { require(raw.length <= max && raw.length == meta.getLong("size") && CatalogUpdates.hash(raw).equals(meta.getString("sha256")), "VISUAL_HASH_MISMATCH"); }
    private static void require(boolean condition, String error) throws IOException { if (!condition) throw new IOException(error); }
    private static JSONObject copy(JSONObject value) throws JSONException { return new JSONObject(value.toString()); }
    private static JSONObject parse(byte[] raw) throws Exception {
        String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(raw)).toString();
        JSONTokener tokener = new JSONTokener(text); Object value = tokener.nextValue(); require(value instanceof JSONObject && tokener.nextClean() == 0, "VISUAL_INVALID"); return (JSONObject)value;
    }
    private static byte[] read(File path, int max) throws IOException { try (InputStream in = new FileInputStream(path)) { return bounded(in, max); } }
    private static byte[] bounded(InputStream in, int max) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] b = new byte[8192]; int n;
        while ((n = in.read(b, 0, Math.min(b.length, max + 1 - out.size()))) != -1) { out.write(b, 0, n); require(out.size() <= max, "VISUAL_TOO_LARGE"); }
        return out.toByteArray();
    }
    private static void atomic(File path, byte[] raw) throws IOException {
        File temp = new File(path.getParentFile(), path.getName() + ".part");
        try {
            try (FileOutputStream out = new FileOutputStream(temp)) { out.write(raw); out.flush(); out.getFD().sync(); }
            Files.move(temp.toPath(), path.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { if (temp.exists()) temp.delete(); }
    }
    private void cleanup() {
        File[] files = directory.listFiles(); if (files == null) return;
        for (File file : files) {
            if (!file.getName().matches("[a-f0-9]{64}") || !file.isDirectory()) continue;
            if ((current != null && file.getName().equals(current.revision)) || (previous != null && file.getName().equals(previous.revision))) continue;
            // Only the fixed, flat content-addressed cache layout is removed.
            File[] blobs = file.listFiles(); if (blobs != null) for (File blob : blobs) if (blob.isFile()) blob.delete();
            file.delete();
        }
    }
    private static byte[] download(String url, int max) throws Exception {
        // Android's platform proxy policy applies; no account headers, cookies or game transport.
        HttpsURLConnection connection = (HttpsURLConnection)new URL(url).openConnection();
        connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(12000); connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json,image/png,image/webp,image/jpeg");
        connection.setRequestProperty("Accept-Encoding", "identity"); connection.setRequestProperty("User-Agent", "StarfallGacha-VisualAssets/1");
        try {
            require(connection.getResponseCode() == 200, "VISUAL_HTTP_ERROR"); require(connection.getContentLengthLong() <= max, "VISUAL_TOO_LARGE");
            String encoding = connection.getContentEncoding(); require(encoding == null || "identity".equalsIgnoreCase(encoding), "VISUAL_INVALID");
            try (InputStream in = connection.getInputStream()) { return bounded(in, max); }
        } finally { connection.disconnect(); }
    }
    private static int little(byte[] raw, int start, int count) { int n = 0; for (int i=0; i<count; i++) n |= (raw[start+i]&255) << (8*i); return n; }
    private static int big(byte[] raw, int start, int count) { int n = 0; for (int i=0; i<count; i++) n = (n << 8) | (raw[start+i]&255); return n; }
    /** Validate raster signatures and canvas bounds before any WebView decode. SVG is never accepted. */
    private static void validateImage(String path, byte[] raw) throws IOException {
        int w = 0, h = 0;
        if (path.endsWith(".png") && raw.length >= 24 && Arrays.equals(Arrays.copyOf(raw, 8), new byte[]{(byte)137,80,78,71,13,10,26,10}) && new String(raw,12,4,StandardCharsets.US_ASCII).equals("IHDR")) {
            w = big(raw,16,4); h = big(raw,20,4);
        } else if (path.endsWith(".webp") && raw.length >= 30 && new String(raw,0,4,StandardCharsets.US_ASCII).equals("RIFF") && new String(raw,8,4,StandardCharsets.US_ASCII).equals("WEBP")) {
            String chunk = new String(raw,12,4,StandardCharsets.US_ASCII);
            if (chunk.equals("VP8X")) { w = 1+little(raw,24,3); h = 1+little(raw,27,3); }
            else if (chunk.equals("VP8L") && raw[20] == 0x2f) { int bits = little(raw,21,4); w=1+(bits&0x3fff); h=1+((bits>>>14)&0x3fff); }
            else if (chunk.equals("VP8 ") && raw.length >= 30 && (raw[23]&255)==0x9d && raw[24]==1 && raw[25]==0x2a) { w=little(raw,26,2)&0x3fff; h=little(raw,28,2)&0x3fff; }
        } else if ((path.endsWith(".jpg") || path.endsWith(".jpeg")) && raw.length >= 4 && (raw[0]&255)==255 && (raw[1]&255)==216) {
            int at=2;
            while (at+4 <= raw.length && (raw[at]&255)==255) {
                int marker=raw[at+1]&255, length=big(raw,at+2,2);
                if (length < 2 || at+2+length > raw.length) break;
                if ((marker>=0xc0 && marker<=0xc3) || (marker>=0xc5 && marker<=0xc7) || (marker>=0xc9 && marker<=0xcb) || (marker>=0xcd && marker<=0xcf)) {
                    if (length>=7) { h=big(raw,at+5,2); w=big(raw,at+7,2); } break;
                }
                at += 2+length;
            }
        }
        require(w > 0 && h > 0 && w <= 4096 && h <= 4096 && (long)w*h <= 16777216L, "VISUAL_IMAGE_INVALID");
    }
}
