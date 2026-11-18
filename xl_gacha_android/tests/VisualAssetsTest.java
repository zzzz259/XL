package cn.starfall.gacha.local;

import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.*;

/** Real filesystem/JSON tests; downloads are deliberately replaced, never production requests. */
public final class VisualAssetsTest {
    static int assertions;
    static File root;
    static final String SOURCE = "https://catalog.example/config/current.json";
    static final byte[] PNG = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=");
    static final byte[] PNG_B = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgZGAAAAAHAALpEtlMAAAAAElFTkSuQmCC");
    static final byte[] PNG_C = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYmAAAAAKAANQonMOAAAAAElFTkSuQmCC");
    static final String A = "assets/portraits/12.png", B = "assets/portraits/13.png", C = "assets/portraits/14.png";
    static void ok(boolean condition, String message) { assertions++; if (!condition) throw new AssertionError(message); }
    static JSONObject bundled() throws Exception {
        return new JSONObject("{\"characters\":{\"11\":{\"path\":\"assets/portraits/11.webp\",\"name\":\"Old\",\"rarity\":5}},\"fashions\":{},\"account_items\":{},\"brand\":{\"logo\":\"assets/brand/logo.png\"},\"files\":{\"assets/portraits/11.webp\":\"bundled\"}}");
    }
    static final class Fake implements VisualAssets.Transport {
        byte[] mapping, image = PNG;
        JSONObject descriptor;
        int calls;
        boolean offline;
        void publish(String version) throws Exception {
            JSONObject files = new JSONObject().put("assets/portraits/12.png", new JSONObject().put("sha256", CatalogUpdates.hash(PNG)).put("size", PNG.length));
            JSONObject m = new JSONObject().put("format", "starfall-gacha-visual-assets").put("schema_version", 1).put("version", version)
                .put("characters", new JSONObject().put("12", new JSONObject().put("path", "assets/portraits/12.png").put("name", "New").put("rarity", 5)))
                .put("fashions", new JSONObject().put("31", new JSONObject().put("path", "assets/portraits/12.png").put("rect_path", "assets/portraits/12.png")))
                .put("account_items", new JSONObject().put("41", new JSONObject().put("type", 108).put("path", "assets/portraits/12.png")))
                .put("files", files);
            publish(m);
        }
        void publish(JSONObject m) throws Exception {
            mapping = m.toString().getBytes(StandardCharsets.UTF_8);
            descriptor = new JSONObject().put("schema_version", 1).put("version", m.getString("version"))
                .put("file", "visual-assets-" + m.getString("version") + ".json").put("sha256", CatalogUpdates.hash(mapping)).put("size", mapping.length);
        }
        JSONObject catalog() throws Exception { return new JSONObject().put("visual_assets", descriptor); }
        public byte[] get(String url, int limit) throws Exception {
            calls++;
            if (offline) throw new IOException("offline");
            if (url.matches("https://catalog.example/config/visual-assets-[1-9][0-9]*\\.json")) return mapping;
            if (url.equals("https://catalog.example/config/assets/portraits/12.png")) return image;
            throw new AssertionError("Unapproved URL: " + url);
        }
    }
    static VisualAssets store(String name, Fake fake) throws Exception { return new VisualAssets(new File(root, name), bundled(), fake); }
    static final class Feed implements VisualAssets.Transport {
        final Fake metadata = new Fake();
        final Map<String, byte[]> images = new LinkedHashMap<>();
        final List<String> requests = new ArrayList<>();
        final String base;
        byte[] failedImage;
        int mappingRequests;

        Feed() { this(SOURCE); }
        Feed(String source) { base = source.substring(0, source.lastIndexOf('/') + 1); }

        void publish(String version, Object... assets) throws Exception {
            images.clear(); requests.clear(); failedImage = null; mappingRequests = 0;
            JSONObject files = new JSONObject(), characters = new JSONObject();
            for (int i = 0; i < assets.length; i += 2) {
                String path = (String) assets[i]; byte[] image = (byte[]) assets[i + 1];
                images.put(path, image);
                files.put(path, new JSONObject().put("sha256", CatalogUpdates.hash(image)).put("size", image.length));
                characters.put(String.valueOf(12 + i / 2), new JSONObject().put("path", path));
            }
            metadata.publish(new JSONObject().put("format", "starfall-gacha-visual-assets").put("schema_version", 1)
                .put("version", version).put("files", files).put("characters", characters));
        }

        public byte[] get(String url, int limit) {
            if (!url.startsWith(base)) throw new AssertionError("Unapproved URL: " + url);
            String path = url.substring(base.length());
            if (path.equals(metadata.descriptor.optString("file"))) {
                mappingRequests++;
                return metadata.mapping;
            }
            if (!images.containsKey(path)) throw new AssertionError("Unapproved URL: " + url);
            requests.add(path);
            return failedImage == null ? images.get(path) : failedImage;
        }
    }

    static void unchangedImagesSurviveUpgrade() throws Exception {
        Feed feed = new Feed(); feed.publish("1", A, PNG, B, PNG_B);
        VisualAssets visual = new VisualAssets(new File(root, "unchanged"), bundled(), feed);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "original snapshot installs");
        String original = visual.status().getString("revision");
        feed.publish("2", A, PNG, B, PNG_B, C, PNG_C);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "additive snapshot installs");
        ok(feed.mappingRequests == 1, "additive version needs exactly one mapping GET");
        ok(feed.requests.equals(Collections.singletonList(C)), "only the new image needs a GET: " + feed.requests);
        try (InputStream in = visual.open("/assets/remote/" + original + "/" + B)) {
            ok(Arrays.equals(PNG_B, read(in)), "prior revision remains independently readable");
        }
    }

    static void restartPreservesHealthyImagesFromDamagedSnapshot() throws Exception {
        Feed feed = new Feed(); feed.publish("1", A, PNG, B, PNG_B);
        File cache = new File(root, "damaged");
        VisualAssets visual = new VisualAssets(cache, bundled(), feed);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "snapshot to repair installs");
        String original = visual.status().getString("revision");
        Files.write(new File(cache, original + "/" + CatalogUpdates.hash(PNG_B)).toPath(), PNG_C);
        visual = new VisualAssets(cache, bundled(), feed);
        ok("bundled".equals(visual.status().getString("revision")) && visual.open("/assets/remote/" + original + "/" + A) == null,
            "partially corrupt snapshot is not served");
        feed.publish("2", A, PNG, B, PNG_B, C, PNG_C);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "new version repairs missing content");
        ok(feed.requests.size() == 2 && feed.requests.containsAll(Arrays.asList(B, C)), "only corrupt and new images need GETs: " + feed.requests);
    }

    static void duplicateContentNeedsOneGet() throws Exception {
        Feed feed = new Feed(); feed.publish("1", A, PNG, B, PNG);
        VisualAssets visual = new VisualAssets(new File(root, "duplicate"), bundled(), feed);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "different paths can contain the same image");
        ok(feed.requests.size() == 1, "duplicate hash needs exactly one GET: " + feed.requests);
        String revision = visual.status().getString("revision");
        for (String path : Arrays.asList(A, B)) try (InputStream in = visual.open("/assets/remote/" + revision + "/" + path)) {
            ok(Arrays.equals(PNG, read(in)), "each path serves the duplicate image");
        }
    }

    static void previousContentCanReplaceCurrentPath() throws Exception {
        Feed feed = new Feed(); feed.publish("1", A, PNG);
        File cache = new File(root, "previous-content");
        VisualAssets visual = new VisualAssets(cache, bundled(), feed);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "previous content installs");
        feed.publish("2", A, PNG_B); feed.failedImage = PNG_C;
        ok("VISUAL_HASH_MISMATCH".equals(visual.check(feed.metadata.catalog(), SOURCE, true).getString("error")), "same path with changed hash verifies remote bytes");
        feed.publish("2", A, PNG_B);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated") && feed.requests.equals(Collections.singletonList(A)),
            "same path with changed hash needs one GET");
        String previous = visual.status().getString("revision");
        visual = new VisualAssets(cache, bundled(), feed);
        feed.publish("3", B, PNG);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "content removed from active snapshot can return");
        ok(feed.requests.isEmpty(), "matching prior hash under a different path needs no GET: " + feed.requests);
        try (InputStream in = visual.open("/assets/remote/" + previous + "/" + A)) {
            ok(Arrays.equals(PNG_B, read(in)), "prior snapshot is retained regardless of content reuse");
        }
    }

    static void bundleContentNeedsNoGet() throws Exception {
        Feed feed = new Feed(); feed.publish("1", B, PNG_B);
        JSONObject baseline = bundled();
        String builtIn = "assets/portraits/11.png";
        baseline.getJSONObject("files").put(builtIn, CatalogUpdates.hash(PNG_B));
        baseline.getJSONObject("files").put("assets/brand/not-a-portrait.png", CatalogUpdates.hash(PNG_C));
        VisualAssets visual = new VisualAssets(new File(root, "built-in"), baseline, feed, path -> {
            if (!path.equals(builtIn)) throw new AssertionError("read unapproved bundled path: " + path);
            return new ByteArrayInputStream(PNG_B);
        });
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "bundle-backed snapshot installs");
        ok(feed.requests.isEmpty(), "same trusted APK hash at another path needs no GET: " + feed.requests);
        feed.publish("2", B, PNG_B, C, PNG_C);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "new same-source snapshot installs");
        ok(feed.requests.equals(Collections.singletonList(C)), "a bundled path outside visual namespaces cannot be reused");

        feed.publish("1", B, PNG_B);
        visual = new VisualAssets(new File(root, "wrong-bundled-bytes"), baseline, feed, path -> new ByteArrayInputStream(PNG_C));
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated") && feed.requests.equals(Collections.singletonList(B)),
            "bundle manifest hash alone is insufficient, recheck actual APK bytes");

        String mismatched = "assets/portraits/13.webp";
        feed.publish("1", mismatched, PNG_B);
        visual = new VisualAssets(new File(root, "wrong-format"), baseline, feed, path -> new ByteArrayInputStream(PNG_B));
        ok("VISUAL_IMAGE_INVALID".equals(visual.check(feed.metadata.catalog(), SOURCE, true).getString("error")),
            "matching SHA cannot bypass declared raster format validation");
    }

    static void corruptedCurrentDoesNotReplaceValidFallback() throws Exception {
        Feed feed = new Feed(); feed.publish("1", A, PNG);
        File cache = new File(root, "valid-fallback");
        VisualAssets visual = new VisualAssets(cache, bundled(), feed);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "first fallback snapshot installs");
        String original = visual.status().getString("revision");
        feed.publish("2", B, PNG_B);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "second fallback snapshot installs");
        Files.write(new File(cache, visual.status().getString("revision") + "/" + CatalogUpdates.hash(PNG_B)).toPath(), PNG_C);
        feed.publish("3", C, PNG_C);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "third fallback snapshot installs");
        Files.write(new File(cache, visual.status().getString("revision") + "/mapping.json").toPath(), new byte[]{1});
        visual = new VisualAssets(cache, bundled(), feed);
        ok(original.equals(visual.status().getString("revision")), "corrupted predecessor cannot displace the last complete fallback");
    }

    static void damagedCachePreservesAcceptedVersion(boolean damagedMapping) throws Exception {
        Feed feed = new Feed(); feed.publish("1", A, PNG);
        File cache = new File(root, "version-floor-" + (damagedMapping ? "mapping" : "image"));
        VisualAssets visual = new VisualAssets(cache, bundled(), feed);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "fallback snapshot installs");
        String fallback = visual.status().getString("revision");
        feed.publish("3", A, PNG, B, PNG_B);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "higher snapshot installs");
        String accepted = visual.status().getString("revision");
        String damagedFile = damagedMapping ? "mapping.json" : CatalogUpdates.hash(PNG_B);
        Files.write(new File(cache, accepted + "/" + damagedFile).toPath(), new byte[]{1});
        visual = new VisualAssets(cache, bundled(), feed);
        ok(fallback.equals(visual.status().getString("revision")), "corrupt current falls back offline");

        feed.publish("2", A, PNG, C, PNG_C);
        ok("VISUAL_DOWNGRADE_REJECTED".equals(visual.check(feed.metadata.catalog(), SOURCE, true).getString("error"))
            && feed.mappingRequests == 0, "fallback does not lower the accepted version");
        feed.publish("3", A, PNG, C, PNG_C);
        ok("VISUAL_VERSION_CONFLICT".equals(visual.check(feed.metadata.catalog(), SOURCE, true).getString("error"))
            && feed.mappingRequests == 0, "fallback does not allow another hash at the accepted version");
        feed.publish("3", A, PNG, B, PNG_B);
        ok(visual.check(feed.metadata.catalog(), SOURCE, true).getBoolean("updated"), "matching descriptor can repair a damaged highest version");
        ok(accepted.equals(visual.status().getString("revision")), "repair restores the accepted revision");
    }

    static void sourceSwitchCancelsOldActivation() throws Exception {
        String nextSource = "https://next.example/updates/current.json";
        Feed original = new Feed(), next = new Feed(nextSource);
        CountDownLatch requestedOldImage = new CountDownLatch(1), releaseOldResponse = new CountDownLatch(1);
        original.publish("1", A, PNG);
        VisualAssets visual = new VisualAssets(new File(root, "source-switch"), bundled(), (url, limit) -> {
            if (url.startsWith(next.base)) return next.get(url, limit);
            if (url.equals(original.base + B)) {
                requestedOldImage.countDown();
                if (!releaseOldResponse.await(5, TimeUnit.SECONDS)) throw new IOException("fixture timed out");
            }
            return original.get(url, limit);
        });
        ok(visual.check(original.metadata.catalog(), SOURCE, true).getBoolean("updated"), "pre-switch snapshot installs");
        String preSwitch = visual.status().getString("revision");
        original.publish("2", B, PNG_B);
        next.publish("3", A, PNG, C, PNG_C);
        visual.start(original.metadata.catalog(), SOURCE, true);
        try {
            ok(requestedOldImage.await(5, TimeUnit.SECONDS), "old source worker started an image GET");
            visual.start(next.metadata.catalog(), nextSource, true);
        } finally { releaseOldResponse.countDown(); }
        for (int i = 0; i < 500 && visual.status().getBoolean("checking"); i++) Thread.sleep(10);
        ok(next.metadata.descriptor.getString("sha256").equals(visual.status().getString("revision")), "only the new source activates after a switch");
        ok(next.requests.equals(Collections.singletonList(C)), "new source reuses local content and supplies the missing image: " + next.requests);
        ok(visual.open("/assets/remote/" + original.metadata.descriptor.getString("sha256") + "/" + B) == null, "obsolete in-flight snapshot never activates");
        try (InputStream in = visual.open("/assets/remote/" + preSwitch + "/" + A)) {
            ok(Arrays.equals(PNG, read(in)), "prior complete snapshot survives source switching");
        }
    }

    public static void main(String[] args) throws Exception {
        assertions = 0;
        root = new File(args[0]); root.mkdirs();
        if (args.length > 1) {
            switch (args[1]) {
                case "unchanged": unchangedImagesSurviveUpgrade(); return;
                case "damaged": restartPreservesHealthyImagesFromDamagedSnapshot(); return;
                case "duplicate": duplicateContentNeedsOneGet(); return;
                case "previous": previousContentCanReplaceCurrentPath(); return;
                case "bundled": bundleContentNeedsNoGet(); return;
                case "fallback": corruptedCurrentDoesNotReplaceValidFallback(); return;
                case "version-floor": damagedCachePreservesAcceptedVersion(false); damagedCachePreservesAcceptedVersion(true); return;
                case "source-switch": sourceSwitchCancelsOldActivation(); return;
                default: throw new IllegalArgumentException(args[1]);
            }
        }
        unchangedImagesSurviveUpgrade();
        restartPreservesHealthyImagesFromDamagedSnapshot();
        duplicateContentNeedsOneGet();
        previousContentCanReplaceCurrentPath();
        bundleContentNeedsNoGet();
        corruptedCurrentDoesNotReplaceValidFallback();
        damagedCachePreservesAcceptedVersion(false);
        damagedCachePreservesAcceptedVersion(true);
        sourceSwitchCancelsOldActivation();
        Fake fake = new Fake(); fake.publish("9007199254740992");
        VisualAssets store = store("good", fake);
        ok("bundled".equals(store.status().getString("revision")), "bundled default");
        ok(store.check(fake.catalog(), SOURCE, true).getBoolean("updated"), "install verified images and mapping");
        String first = store.status().getString("revision");
        JSONObject effective = store.manifest();
        String path = effective.getJSONObject("characters").getJSONObject("12").getString("path");
        ok(path.equals("assets/remote/" + first + "/assets/portraits/12.png"), "immutable revision URL");
        ok(effective.getJSONObject("characters").has("11"), "bundled characters remain fallback");
        ok(effective.getJSONObject("brand").getString("logo").equals("assets/brand/logo.png"), "bundled brand preserved");
        ok(effective.getJSONObject("files").has(path), "effective file index includes immutable URLs");
        try (InputStream in = store.open("/" + path)) { ok(Arrays.equals(PNG, read(in)), "serve verified raster bytes"); }
        ok(store.open("/assets/remote/" + first + "/../source.json") == null, "cannot read private files");
        int before = fake.calls;
        store.check(fake.catalog(), SOURCE, false);
        ok(fake.calls == before, "same descriptor avoids downloads");
        fake.publish("9007199254740993"); JSONObject next = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        next.getJSONObject("files").getJSONObject(A).put("sha256", CatalogUpdates.hash(PNG_B)).put("size", PNG_B.length);
        fake.publish(next); fake.image = new byte[]{1};
        ok("VISUAL_HASH_MISMATCH".equals(store.check(fake.catalog(), SOURCE, true).getString("error")), "bad image hash rejected");
        ok(first.equals(store.status().getString("revision")), "failed image preserves active map");
        fake.image = PNG_B;
        ok(store.check(fake.catalog(), SOURCE, true).getBoolean("updated"), "retry completes update");
        String second = store.status().getString("revision");
        try (InputStream in = store.open("/" + path)) { ok(Arrays.equals(PNG, read(in)), "previous revision remains available to rendered pages"); }
        fake.publish("9007199254740992");
        ok("VISUAL_DOWNGRADE_REJECTED".equals(store.check(fake.catalog(), SOURCE, true).getString("error")), "exact decimal version ordering");
        fake.publish(next); JSONObject changed = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        changed.getJSONObject("characters").getJSONObject("12").put("name", "Changed"); fake.publish(changed);
        ok("VISUAL_VERSION_CONFLICT".equals(store.check(fake.catalog(), SOURCE, true).getString("error")), "immutable version");
        fake.offline = true;
        ok(second.equals(store("good", fake).status().getString("revision")), "offline restart restores verified cache");
        Files.write(new File(root, "good/active.json").toPath(), new byte[]{1});
        ok(first.equals(store("good", fake).status().getString("revision")), "corrupt pointer restores previous");
        ok(store.check(new JSONObject(), SOURCE, true).getString("phase").equals("unconfigured"), "legacy catalog opts out without requests");
        fake.offline = false; fake.image = PNG; fake.publish("9007199254740994");
        for (String evil : new String[]{"assets/portraits/../x.png", "assets/portraits/%2e.png", "assets/account/x.svg", "assets/account/x.js", "https://evil/x.png", "assets/brand/x.png"}) {
            JSONObject m = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
            m.getJSONObject("characters").getJSONObject("12").put("path", evil); fake.publish(m);
            ok(!store("unsafe-" + assertions, fake).check(fake.catalog(), SOURCE, true).getBoolean("updated"), "unsafe path rejected: " + evil);
        }
        fake.publish("9007199254740994"); fake.mapping[0] = 'x';
        ok("VISUAL_HASH_MISMATCH".equals(store("maphash", fake).check(fake.catalog(), SOURCE, true).getString("error")), "mapping hash verified before parsing");
        fake.publish("9007199254740994");
        int offlineBefore = fake.calls; fake.offline = true;
        VisualAssets offline = store("offline", fake); offline.check(fake.catalog(), SOURCE, true); int failedCalls = fake.calls;
        offline.check(fake.catalog(), SOURCE, false);
        ok(failedCalls == offlineBefore + 1 && fake.calls == failedCalls && "bundled".equals(offline.status().getString("revision")), "offline download preserves bundle and backs off"); fake.offline = false;
        offline.start(fake.catalog(), SOURCE, false);
        for (int i=0; i<200 && offline.status().getBoolean("checking"); i++) Thread.sleep(10);
        ok("error".equals(offline.status().getString("phase")) && !offline.status().getBoolean("checking"), "backoff cannot leave a settled worker in checking phase");
        fake.publish("9007199254740994");
        JSONObject invalidRaster = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        fake.image = "<script>bad()</script>".getBytes(StandardCharsets.UTF_8);
        invalidRaster.getJSONObject("files").getJSONObject("assets/portraits/12.png").put("sha256", CatalogUpdates.hash(fake.image)).put("size", fake.image.length);
        fake.publish(invalidRaster);
        ok("VISUAL_IMAGE_INVALID".equals(store("raster-only", fake).check(fake.catalog(), SOURCE, true).getString("error")), "non-raster bytes rejected even when correctly hashed");
        fake.image = PNG; fake.publish("9007199254740994");
        JSONObject extraCode = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8)); extraCode.put("scripts", "assets/hotfix.js"); fake.publish(extraCode);
        ok(!store("no-code", fake).check(fake.catalog(), SOURCE, true).getBoolean("updated"), "remote mapping cannot introduce scripts or UI metadata");
        fake.publish("9007199254740994");
        JSONObject huge = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        huge.getJSONObject("files").getJSONObject("assets/portraits/12.png").put("size", 4 * 1024 * 1024 + 1); fake.publish(huge);
        ok(!store("oversized", fake).check(fake.catalog(), SOURCE, true).getBoolean("updated"), "declared image size bounded");
        fake.publish("9007199254740994");
        JSONObject missing = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8)); missing.getJSONObject("files").remove("assets/portraits/12.png"); fake.publish(missing);
        ok(!store("missing", fake).check(fake.catalog(), SOURCE, true).getBoolean("updated"), "every remote reference declared");
        fake.publish("9007199254740994");
        JSONObject nullEffect = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        nullEffect.getJSONObject("account_items").put("42", new JSONObject().put("type", 109).put("path", "assets/portraits/12.png").put("effect_value", JSONObject.NULL));
        fake.publish(nullEffect);
        ok(store("null-effect", fake).check(fake.catalog(), SOURCE, true).getBoolean("updated"), "permanent frames accept null expiry effect like bundled data");
        fake.publish("9007199254740994");
        VisualAssets repair = store("repair", fake); repair.check(fake.catalog(), SOURCE, true);
        Files.write(new File(root, "repair/" + fake.descriptor.getString("sha256") + "/" + CatalogUpdates.hash(PNG)).toPath(), new byte[]{1});
        ok(repair.check(fake.catalog(), SOURCE, true).getBoolean("updated"), "forced check repairs damaged image at same version");
        ok(repair.status().getString("revision").equals(store("repair", fake).status().getString("revision")), "repaired snapshot survives restart");
        fake.publish("9007199254740994");
        VisualAssets rollback = store("rollback", fake); rollback.check(fake.catalog(), SOURCE, true); String fallbackRevision=rollback.status().getString("revision");
        fake.publish("9007199254740995"); rollback.check(fake.catalog(), SOURCE, true); String brokenRevision=rollback.status().getString("revision");
        Files.write(new File(root, "rollback/" + brokenRevision + "/mapping.json").toPath(), new byte[]{1});
        ok(fallbackRevision.equals(store("rollback", fake).status().getString("revision")), "corrupt current mapping restores previous verified snapshot");
        Files.write(new File(root, "rollback/" + fallbackRevision + "/mapping.json").toPath(), new byte[]{1});
        ok("bundled".equals(store("rollback", fake).status().getString("revision")), "corruption of all caches restores bundled mapping");
        fake.publish("9007199254740996");
        JSONObject cropped = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        cropped.getJSONObject("characters").getJSONObject("12").put("source_size", new JSONArray("[256,256]")).put("crop_rect", new JSONArray("[38,0,180,180]"));
        cropped.getJSONObject("fashions").getJSONObject("31").put("source_size", new JSONArray("[320,140]")).put("crop_rect", new JSONArray("[91,23,88,88]"));
        fake.publish(cropped); VisualAssets crops = store("crops", fake);
        ok(crops.check(fake.catalog(), SOURCE, true).getBoolean("updated"), "bounded crop metadata activates");
        JSONObject activeCrop = crops.manifest().getJSONObject("fashions").getJSONObject("31");
        ok(activeCrop.has("crop_rect") && activeCrop.getJSONArray("crop_rect").getInt(0) == 91, "effective manifest retains native crop metadata");
        cropped.getJSONObject("fashions").getJSONObject("31").put("crop_rect", new JSONArray("[319,0,88,88]")); fake.publish(cropped);
        ok(!store("crop-outside", fake).check(fake.catalog(), SOURCE, true).getBoolean("updated"), "crop cannot extend outside source canvas");
        fake.publish("9007199254740994");
        final Fake slow = fake; final CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        VisualAssets concurrent = new VisualAssets(new File(root, "concurrent"), bundled(), (url, limit) -> { entered.countDown(); release.await(5, TimeUnit.SECONDS); return slow.get(url, limit); });
        concurrent.start(fake.catalog(), SOURCE, true); ok(entered.await(5, TimeUnit.SECONDS), "worker started");
        concurrent.start(new JSONObject(), "", true); release.countDown();
        for (int i=0; i<200 && concurrent.status().getBoolean("checking"); i++) Thread.sleep(10);
        ok("bundled".equals(concurrent.status().getString("revision")), "source/descriptor switch rejects in-flight activation");
        System.out.println("VisualAssets checks passed: " + assertions);
    }
    static byte[] read(InputStream in) throws Exception { ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] b = new byte[1024]; int n; while ((n = in.read(b)) != -1) out.write(b,0,n); return out.toByteArray(); }
}
