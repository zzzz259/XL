package cn.starfall.gacha.local;

import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import javax.net.ssl.HttpsURLConnection;

/** Data-only, credential-free catalog updater. All committed snapshots are immutable. */
public final class CatalogUpdates {
    static final int MAX_CATALOG = 4 * 1024 * 1024, MAX_MANIFEST = 16 * 1024;
    private static final long INTERVAL = 6 * 3600 * 1000L, BACKOFF = 60 * 1000L;
    private static final long MAX_ID = 9007199254740991L;
    interface Transport { byte[] get(String url, int limit) throws Exception; }
    private final File directory;
    private final Transport transport;
    private JSONObject current, pointer;
    private String revision, source, phase = "idle", error = "", updatedAt = "";
    private long checkedAt, retryAt;
    private boolean remote, busy;

    public CatalogUpdates(File cacheDirectory, JSONObject bundledCatalog, String defaultManifestUrl) throws Exception {
        this(cacheDirectory, bundledCatalog, defaultManifestUrl, CatalogUpdates::download);
    }

    CatalogUpdates(File cacheDirectory, JSONObject bundledCatalog, String defaultManifestUrl, Transport transport) throws Exception {
        directory = cacheDirectory; this.transport = transport;
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("CATALOG_CACHE_UNAVAILABLE");
        current = copy(bundledCatalog);
        validateCatalog(current, true);
        revision = hash(current.toString().getBytes(StandardCharsets.UTF_8));
        source = validateSource(defaultManifestUrl == null ? "" : defaultManifestUrl);
        try { source = validateSource(readJson(new File(directory, "source.json"), MAX_MANIFEST).getString("url")); }
        catch (Exception ignored) { }
        boolean restored = false;
        try {
            JSONObject state = readJson(new File(directory, "active.json"), MAX_MANIFEST);
            restored = restore(state.optJSONObject("current"));
            if (!restored) restored = restore(state.optJSONObject("previous"));
        } catch (Exception ignored) { }
        if (!restored) {
            try { restore(readJson(new File(directory, "previous.json"), MAX_MANIFEST)); }
            catch (Exception ignored) { }
        }
        try {
            JSONObject state = readJson(new File(directory, "check.json"), MAX_MANIFEST);
            if (source.equals(state.getString("source"))) {
                long now = System.currentTimeMillis();
                checkedAt = Math.min(now, state.optLong("checked_at", 0));
                retryAt = Math.min(now + BACKOFF, state.optLong("retry_at", 0));
            }
        } catch (Exception ignored) { }
        if (source.isEmpty()) phase = "unconfigured";
    }

    public synchronized JSONObject catalog() {
        try { return copy(current).put("_revision", revision); }
        catch (JSONException e) { throw new IllegalStateException(e); }
    }

    public synchronized JSONObject status() {
        try {
            return new JSONObject().put("phase", phase).put("version", current.getString("version"))
                .put("revision", revision).put("source", source).put("updated_at", updatedAt)
                .put("error", error).put("checked_at", checkedAt).put("retry_at", retryAt)
                .put("remote", remote).put("origin", remote ? "cache" : "bundled").put("configured", !source.isEmpty()).put("checking", busy);
        } catch (JSONException e) { throw new IllegalStateException(e); }
    }

    public synchronized JSONObject setSource(String url) throws Exception {
        String value = validateSource(url == null ? "" : url.trim());
        atomic(new File(directory, "source.json"), new JSONObject().put("url", value).toString().getBytes(StandardCharsets.UTF_8));
        source = value; checkedAt = 0; retryAt = 0; error = "";
        if (!busy) phase = value.isEmpty() ? "unconfigured" : "idle";
        saveCheck();
        return status();
    }

    public void start(final boolean force) {
        synchronized (this) { if (busy) return; busy = true; phase = "checking"; }
        Thread worker = new Thread(() -> runCheck(force), "catalog-update");
        worker.setDaemon(true); worker.start();
    }

    public JSONObject check(boolean force) {
        synchronized (this) {
            if (busy) return result(false);
            busy = true;
        }
        return runCheck(force);
    }

    private JSONObject runCheck(boolean force) {
        String requestedSource;
        synchronized (this) {
            requestedSource = source;
            long now = System.currentTimeMillis();
            if (source.isEmpty() || (!force && (now < retryAt || (checkedAt > 0 && now - checkedAt < INTERVAL)))) {
                busy = false;
                if (source.isEmpty()) phase = "unconfigured";
                else if (phase.equals("checking")) phase = error.isEmpty() ? "current" : "error";
                return result(false);
            }
            phase = "checking"; error = "";
        }
        boolean updated = false;
        try {
            byte[] rawManifest = transport.get(requestedSource, MAX_MANIFEST);
            require(rawManifest.length <= MAX_MANIFEST, "CATALOG_TOO_LARGE");
            JSONObject incoming = parse(rawManifest); validateManifest(incoming);
            JSONObject manifest = new JSONObject();
            for (String key : new String[]{"format", "schema_version", "version", "file", "sha256", "size", "published_at"}) {
                if (incoming.has(key)) manifest.put(key, incoming.get(key));
            }
            String version = manifest.getString("version");
            synchronized (this) {
                require(source.equals(requestedSource), "CATALOG_SOURCE_CHANGED");
                require(compare(version, current.getString("version")) >= 0, "CATALOG_DOWNGRADE_REJECTED");
                if (remote && version.equals(current.getString("version"))) {
                    require(revision.equals(manifest.getString("sha256")), "CATALOG_VERSION_CONFLICT");
                    success(); busy = false; return result(false);
                }
            }
            String target = new URI(requestedSource).resolve(manifest.getString("file")).toString();
            byte[] raw = transport.get(target, MAX_CATALOG);
            JSONObject candidate = verified(raw, manifest);
            synchronized (this) {
                require(source.equals(requestedSource), "CATALOG_SOURCE_CHANGED");
                String sha = manifest.getString("sha256");
                File dataFile = new File(directory, sha + ".json");
                // Immutable data first, pointer last; a failed commit leaves the prior snapshot intact.
                // The downloaded bytes are already hash/schema verified. Repair
                // a truncated local blob rather than rejecting this version forever.
                atomic(dataFile, raw);
                String saved = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US) {{ setTimeZone(TimeZone.getTimeZone("UTC")); }}.format(new Date());
                JSONObject next = new JSONObject().put("manifest", manifest).put("updated_at", saved);
                JSONObject state = new JSONObject().put("current", next).put("previous", pointer == null ? JSONObject.NULL : pointer);
                if (pointer != null) atomic(new File(directory, "previous.json"), pointer.toString().getBytes(StandardCharsets.UTF_8));
                atomic(new File(directory, "active.json"), state.toString().getBytes(StandardCharsets.UTF_8));
                current = candidate; revision = sha; pointer = next; remote = true; updatedAt = saved; updated = true;
                success();
            }
        } catch (Exception e) {
            synchronized (this) {
                phase = "error";
                String message = e.getMessage();
                error = message != null && message.matches("CATALOG_[A-Z_]+") ? message : "CATALOG_UPDATE_FAILED";
                retryAt = System.currentTimeMillis() + BACKOFF; saveCheck();
            }
        } finally { synchronized (this) { busy = false; } }
        synchronized (this) { return result(updated); }
    }

    private void success() {
        checkedAt = System.currentTimeMillis(); retryAt = 0; phase = "current"; error = ""; saveCheck();
    }
    private void saveCheck() {
        try { atomic(new File(directory, "check.json"), new JSONObject().put("source", source).put("checked_at", checkedAt).put("retry_at", retryAt).toString().getBytes(StandardCharsets.UTF_8)); }
        catch (Exception ignored) { }
    }
    private JSONObject result(boolean updated) {
        try { return status().put("updated", updated); } catch (JSONException e) { throw new IllegalStateException(e); }
    }
    private boolean restore(JSONObject entry) {
        try {
            JSONObject manifest = entry.getJSONObject("manifest"); validateManifest(manifest);
            require(compare(manifest.getString("version"), current.getString("version")) >= 0, "CATALOG_DOWNGRADE_REJECTED");
            JSONObject data = verified(read(new File(directory, manifest.getString("sha256") + ".json"), MAX_CATALOG), manifest);
            current = data; revision = manifest.getString("sha256"); remote = true;
            updatedAt = entry.optString("updated_at", ""); pointer = entry; return true;
        } catch (Exception ignored) { return false; }
    }

    static String validateSource(String value) throws Exception {
        if (value.isEmpty()) return value;
        URI uri;
        try { uri = new URI(value); }
        catch (URISyntaxException e) { throw new IOException("CATALOG_SOURCE_INVALID"); }
        require(value.length() <= 2048 && value.indexOf('\\') < 0 && !value.matches(".*[\\s\\p{Cntrl}].*"), "CATALOG_SOURCE_INVALID");
        require("https".equals(uri.getScheme()) && uri.getHost() != null && uri.getRawUserInfo() == null && uri.getRawQuery() == null && uri.getRawFragment() == null && (uri.getPort() == -1 || (uri.getPort() > 0 && uri.getPort() <= 65535)), "CATALOG_SOURCE_INVALID");
        String path = uri.getRawPath();
        require(path != null && path.endsWith("/current.json") && !path.contains("%") && !path.contains("//") && uri.normalize().equals(uri), "CATALOG_SOURCE_INVALID");
        return value;
    }

    static void validateManifest(JSONObject m) throws Exception {
        require("starfall-gacha-catalog-manifest".equals(m.opt("format")), "CATALOG_SCHEMA_UNSUPPORTED");
        require((m.opt("schema_version") instanceof Integer || m.opt("schema_version") instanceof Long) && ((Number)m.opt("schema_version")).longValue() == 1, "CATALOG_SCHEMA_UNSUPPORTED"); version(m.opt("version"));
        require(("catalog-" + m.getString("version") + ".json").equals(m.opt("file")), "CATALOG_INVALID");
        require(m.opt("sha256") instanceof String && m.getString("sha256").matches("[a-f0-9]{64}"), "CATALOG_INVALID");
        integer(m.opt("size"), 1, MAX_CATALOG);
        if (m.has("published_at")) require(m.opt("published_at") instanceof String && m.getString("published_at").length() <= 120, "CATALOG_INVALID");
    }
    static void validateCatalog(JSONObject c, boolean bundled) throws Exception {
        if (bundled && !c.has("schema_version")) c.put("schema_version", 1);
        require((c.opt("schema_version") instanceof Integer || c.opt("schema_version") instanceof Long) && ((Number)c.opt("schema_version")).longValue() == 1, "CATALOG_SCHEMA_UNSUPPORTED"); version(c.opt("version"));
        JSONObject cards = c.getJSONObject("cards"), pools = c.getJSONObject("pools"), meta = c.getJSONObject("pool_meta");
        require(cards.length() > 0 && cards.length() <= 20000 && pools.length() > 0 && pools.length() <= 20000 && meta.length() == pools.length(), "CATALOG_INVALID");
        Iterator<String> keys = cards.keys();
        while (keys.hasNext()) {
            String key = keys.next(); id(key); JSONArray card = cards.getJSONArray(key);
            require(card.length() == 2, "CATALOG_INVALID"); label(card.get(0));
            if (!card.isNull(1)) integer(card.get(1), 1, 5);
        }
        keys = pools.keys();
        while (keys.hasNext()) {
            String key = keys.next(); id(key); label(pools.get(key)); JSONObject m = meta.getJSONObject(key);
            require(Arrays.asList("normal", "limited", "collection", "beginner", "unknown").contains(m.opt("category")), "CATALOG_INVALID");
            require(m.opt("pity_group") instanceof String && m.getString("pity_group").matches("[A-Za-z0-9:_-]{1,80}"), "CATALOG_INVALID");
            require(m.has("hard_pity"), "CATALOG_INVALID");
            if (!m.isNull("hard_pity")) integer(m.get("hard_pity"), 1, 1000);
            JSONArray ids = m.getJSONArray("featured_ids"), names = m.getJSONArray("featured_names");
            require(ids.length() <= 128 && ids.length() == names.length(), "CATALOG_INVALID");
            Set<Long> seen = new HashSet<>();
            for (int i = 0; i < ids.length(); i++) {
                long featured = integer(ids.get(i), 1, MAX_ID);
                require(seen.add(featured), "CATALOG_INVALID");
                JSONArray card = cards.getJSONArray(Long.toString(featured));
                require(Long.valueOf(5).equals(card.isNull(1) ? null : integer(card.get(1), 1, 5)) && card.getString(0).equals(names.get(i)), "CATALOG_INVALID");
            }
        }
        JSONArray selectors = c.getJSONArray("selectors");
        require(selectors.length() >= 1 && selectors.length() <= 8, "CATALOG_INVALID");
        Set<Long> seen = new HashSet<>();
        for (int i = 0; i < selectors.length(); i++) require(seen.add(integer(selectors.get(i), 1, MAX_ID)), "CATALOG_INVALID");
    }
    private static JSONObject verified(byte[] raw, JSONObject manifest) throws Exception {
        require(raw.length <= MAX_CATALOG && raw.length == manifest.getLong("size") && hash(raw).equals(manifest.getString("sha256")), "CATALOG_HASH_MISMATCH");
        JSONObject c = parse(raw); validateCatalog(c, false);
        require(c.getString("version").equals(manifest.getString("version")), "CATALOG_VERSION_MISMATCH");
        return c;
    }
    private static void version(Object v) throws IOException { require(v instanceof String && ((String)v).matches("[1-9][0-9]{0,19}"), "CATALOG_INVALID"); }
    private static int compare(String a, String b) { return a.length() == b.length() ? a.compareTo(b) : Integer.compare(a.length(), b.length()); }
    private static void id(String value) throws Exception { require(value.matches("[1-9][0-9]{0,15}"), "CATALOG_INVALID"); integer(Long.parseLong(value), 1, MAX_ID); }
    private static long integer(Object value, long min, long max) throws IOException {
        require(value instanceof Integer || value instanceof Long, "CATALOG_INVALID");
        long n = ((Number)value).longValue(); require(n >= min && n <= max, "CATALOG_INVALID"); return n;
    }
    private static void label(Object value) throws IOException {
        require(value instanceof String, "CATALOG_INVALID"); String s = (String)value;
        require(!s.trim().isEmpty() && s.codePointCount(0, s.length()) <= 120, "CATALOG_INVALID");
        for (int i = 0; i < s.length(); i++) require(!Character.isISOControl(s.charAt(i)), "CATALOG_INVALID");
    }
    private static void require(boolean condition, String code) throws IOException { if (!condition) throw new IOException(code); }
    private static JSONObject copy(JSONObject value) throws JSONException { return new JSONObject(value.toString()); }
    static String hash(byte[] raw) throws Exception {
        StringBuilder b = new StringBuilder(); for (byte v : MessageDigest.getInstance("SHA-256").digest(raw)) b.append(String.format(Locale.US, "%02x", v & 255)); return b.toString();
    }
    private static JSONObject parse(byte[] raw) throws Exception {
        String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(raw)).toString();
        JSONTokener tokener = new JSONTokener(text); Object value = tokener.nextValue();
        require(value instanceof JSONObject && tokener.nextClean() == 0, "CATALOG_INVALID"); return (JSONObject)value;
    }
    private static JSONObject readJson(File path, int limit) throws Exception { return parse(read(path, limit)); }
    private static byte[] read(File path, int limit) throws IOException { try (InputStream in = new FileInputStream(path)) { return bounded(in, limit); } }
    private static byte[] bounded(InputStream in, int limit) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] buffer = new byte[8192]; int n;
        while ((n = in.read(buffer, 0, Math.min(buffer.length, limit + 1 - out.size()))) != -1) {
            out.write(buffer, 0, n); require(out.size() <= limit, "CATALOG_TOO_LARGE");
        }
        return out.toByteArray();
    }
    private static void atomic(File file, byte[] raw) throws IOException {
        File temp = new File(file.getParentFile(), file.getName() + ".part");
        try {
            try (FileOutputStream out = new FileOutputStream(temp)) { out.write(raw); out.flush(); out.getFD().sync(); }
            Files.move(temp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { if (temp.exists()) temp.delete(); }
    }
    private static byte[] download(String source, int limit) throws Exception {
        HttpsURLConnection connection = (HttpsURLConnection)new URL(source).openConnection(Proxy.NO_PROXY);
        connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(12000);
        connection.setRequestProperty("Accept", "application/json"); connection.setRequestProperty("Accept-Encoding", "identity");
        connection.setRequestProperty("User-Agent", "StarfallGacha-Catalog/1"); connection.setUseCaches(false);
        try {
            require(connection.getResponseCode() == 200, "CATALOG_HTTP_ERROR");
            require(connection.getContentLengthLong() <= limit, "CATALOG_TOO_LARGE");
            String encoding = connection.getContentEncoding(); require(encoding == null || "identity".equalsIgnoreCase(encoding), "CATALOG_INVALID");
            try (InputStream in = connection.getInputStream()) { return bounded(in, limit); }
        } finally { connection.disconnect(); }
    }
}
