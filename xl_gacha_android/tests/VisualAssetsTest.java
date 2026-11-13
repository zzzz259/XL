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
    public static void main(String[] args) throws Exception {
        root = new File(args[0]); root.mkdirs();
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
        fake.publish("9007199254740993"); fake.image = new byte[]{1};
        ok("VISUAL_HASH_MISMATCH".equals(store.check(fake.catalog(), SOURCE, true).getString("error")), "bad image hash rejected");
        ok(first.equals(store.status().getString("revision")), "failed image preserves active map");
        fake.image = PNG;
        ok(store.check(fake.catalog(), SOURCE, true).getBoolean("updated"), "retry completes update");
        String second = store.status().getString("revision");
        try (InputStream in = store.open("/" + path)) { ok(Arrays.equals(PNG, read(in)), "previous revision remains available to rendered pages"); }
        fake.publish("9007199254740992");
        ok("VISUAL_DOWNGRADE_REJECTED".equals(store.check(fake.catalog(), SOURCE, true).getString("error")), "exact decimal version ordering");
        fake.publish("9007199254740993"); JSONObject changed = new JSONObject(new String(fake.mapping, StandardCharsets.UTF_8));
        changed.getJSONObject("characters").getJSONObject("12").put("name", "Changed"); fake.publish(changed);
        ok("VISUAL_VERSION_CONFLICT".equals(store.check(fake.catalog(), SOURCE, true).getString("error")), "immutable version");
        fake.offline = true;
        ok(second.equals(store("good", fake).status().getString("revision")), "offline restart restores verified cache");
        Files.write(new File(root, "good/active.json").toPath(), new byte[]{1});
        ok(first.equals(store("good", fake).status().getString("revision")), "corrupt pointer restores previous");
        ok(store.check(new JSONObject(), SOURCE, true).getString("phase").equals("unconfigured"), "legacy catalog opts out without requests");
        fake.offline = false; fake.publish("9007199254740994");
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
