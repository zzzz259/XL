package cn.starfall.gacha.local;

import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Runs against Android's real org.json via app_process (no stub JSON implementation). */
public final class CatalogUpdatesTest {
    static int assertions;
    static File root;
    static final String SOURCE = "https://catalog.example/config/current.json";
    static void ok(boolean condition, String message) { assertions++; if (!condition) throw new AssertionError(message); }
    interface Action { void run() throws Exception; }
    static void rejects(Action action, String message) throws Exception {
        try { action.run(); } catch (Exception expected) { assertions++; return; }
        throw new AssertionError(message);
    }
    static JSONObject catalog(String version) throws Exception {
        return new JSONObject("{\"schema_version\":1,\"version\":\"" + version + "\",\"cards\":{\"11\":[\"One\",5],\"12\":[\"Two\",null]},\"pools\":{\"21\":\"Pool\"},\"pool_meta\":{\"21\":{\"category\":\"limited\",\"pity_group\":\"limited\",\"featured_ids\":[11],\"featured_names\":[\"One\"],\"hard_pity\":70}},\"selectors\":[1,2]}");
    }
    static final class Fake implements CatalogUpdates.Transport {
        byte[] body, manifest; int calls; boolean fail;
        void publish(String version) throws Exception { publish(catalog(version)); }
        void publish(JSONObject data) throws Exception {
            body = data.toString().getBytes(StandardCharsets.UTF_8);
            manifest = new JSONObject().put("format", "starfall-gacha-catalog-manifest").put("schema_version", 1)
                .put("version", data.getString("version")).put("file", "catalog-" + data.getString("version") + ".json")
                .put("size", body.length).put("sha256", CatalogUpdates.hash(body)).toString().getBytes(StandardCharsets.UTF_8);
        }
        public byte[] get(String url, int limit) throws Exception {
            calls++; if (fail) throw new IOException("offline");
            ok(url.equals(SOURCE) || url.matches("https://catalog.example/config/catalog-[1-9][0-9]*\\.json"), "fixed directory URLs");
            return url.endsWith("current.json") ? manifest : body;
        }
    }
    static File directory(String name) { File f = new File(root, name); f.mkdirs(); return f; }
    static CatalogUpdates store(File directory, Fake fake) throws Exception { return new CatalogUpdates(directory, catalog("9007199254740992"), SOURCE, fake); }

    public static void main(String[] args) throws Exception {
        root = new File(args[0]); root.mkdirs();
        if (args.length > 1) {
            JSONObject shared = new JSONObject(new String(Files.readAllBytes(new File(args[1]).toPath()), StandardCharsets.UTF_8));
            validateShared(shared);
        }
        for (String url : new String[]{"http://catalog.example/config/current.json", "https://user@catalog.example/config/current.json", "https://catalog.example/config/current.json?uid=1", "https://catalog.example/config/current.json#x", "https://catalog.example:65536/config/current.json", "https://catalog.example/a/../current.json", "https://catalog.example/%2e/current.json", "https://catalog.example/config/catalog.json"})
            rejects(() -> CatalogUpdates.validateSource(url), "unsafe source accepted");
        ok(SOURCE.equals(CatalogUpdates.validateSource(SOURCE)), "source accepted");
        ok(CatalogUpdates.validateSource("https://catalog.example:8443/config/current.json").contains(":8443"), "explicit HTTPS port");
        JSONObject valid = catalog("99999999999999999999"); CatalogUpdates.validateCatalog(valid, false);
        JSONObject bad = catalog("1"); bad.put("schema_version", 2); final JSONObject schema = bad;
        rejects(() -> CatalogUpdates.validateCatalog(schema, false), "unknown schema");
        for (String version : new String[]{"0", "01", "-1", "100000000000000000000", "1.5"}) {
            final JSONObject c = catalog(version); rejects(() -> CatalogUpdates.validateCatalog(c, false), "invalid decimal version");
        }
        bad = catalog("1"); bad.put("version", 1); final JSONObject numberVersion = bad;
        rejects(() -> CatalogUpdates.validateCatalog(numberVersion, false), "numeric version accepted");
        bad = catalog("1"); bad.getJSONObject("cards").put("01", new JSONArray("[\"Bad\",5]")); final JSONObject badId = bad;
        rejects(() -> CatalogUpdates.validateCatalog(badId, false), "invalid ID");
        bad = catalog("1"); bad.getJSONObject("cards").getJSONArray("11").put(0, "A\nB"); final JSONObject control = bad;
        rejects(() -> CatalogUpdates.validateCatalog(control, false), "control name accepted");
        bad = catalog("1"); bad.getJSONObject("pool_meta").getJSONObject("21").getJSONArray("featured_names").put(0, "Mismatch"); final JSONObject refs = bad;
        rejects(() -> CatalogUpdates.validateCatalog(refs, false), "mismatched featured name");
        bad = catalog("1"); bad.getJSONArray("selectors").put(1, 1); final JSONObject duplicates = bad;
        rejects(() -> CatalogUpdates.validateCatalog(duplicates, false), "duplicate selectors");
        bad = catalog("1"); bad.getJSONObject("pool_meta").put("22", new JSONObject()); final JSONObject extra = bad;
        rejects(() -> CatalogUpdates.validateCatalog(extra, false), "extra pool metadata");
        Fake fake = new Fake(); File cache = directory("cache"); CatalogUpdates store = store(cache, fake);
        String baseline = store.catalog().getString("_revision");
        store.catalog().getJSONObject("cards").remove("11"); ok(store.catalog().getJSONObject("cards").has("11"), "snapshot isolation");
        fake.publish("9007199254740992"); ok(store.check(true).getBoolean("updated"), "same baseline version first remote install");
        String first = store.catalog().getString("_revision");
        ok(first.length() == 64, "SHA256 revision");
        int before = fake.calls; ok(!store.check(false).getBoolean("updated") && fake.calls == before, "six hour throttle");
        ok(!store.check(true).getBoolean("updated") && fake.calls == before + 1, "same remote version only fetches manifest");
        JSONObject changed = catalog("9007199254740992"); changed.getJSONObject("cards").getJSONArray("12").put(0, "Changed"); fake.publish(changed);
        ok("CATALOG_VERSION_CONFLICT".equals(store.check(true).getString("error")), "same remote version mutation blocked");
        ok(first.equals(store.catalog().getString("_revision")), "mutation preserves snapshot");
        fake.publish("9007199254740993"); ok(store.check(true).getBoolean("updated"), "adjacent large version ordering exact");
        String second = store.catalog().getString("_revision");
        fake.publish("9007199254740992"); ok("CATALOG_DOWNGRADE_REJECTED".equals(store.check(true).getString("error")), "downgrade blocked");
        fake.publish("9007199254740994"); fake.body[0] = 'x';
        ok("CATALOG_HASH_MISMATCH".equals(store.check(true).getString("error")), "raw hash validation");
        ok(second.equals(store.catalog().getString("_revision")), "failed check preserves snapshot");
        before = fake.calls; store.check(false); ok(before == fake.calls, "failure throttle");
        fake.fail = true; store.check(true); ok(second.equals(store.catalog().getString("_revision")), "offline fallback");
        CatalogUpdates reopened = store(cache, fake); ok(second.equals(reopened.catalog().getString("_revision")), "restart cache");
        byte[] active = Files.readAllBytes(new File(cache, "active.json").toPath());
        Files.write(new File(cache, "active.json").toPath(), new byte[]{1});
        ok(first.equals(store(cache, fake).catalog().getString("_revision")), "corrupt pointer falls back independent previous pointer");
        Files.write(new File(cache, "active.json").toPath(), active);
        Files.write(new File(cache, second + ".json").toPath(), new byte[]{1});
        CatalogUpdates fallback = store(cache, fake); ok(first.equals(fallback.catalog().getString("_revision")), "corrupt current falls back previous");
        Files.write(new File(cache, first + ".json").toPath(), new byte[]{1});
        ok(baseline.equals(store(cache, fake).catalog().getString("_revision")), "all corrupt falls back bundled");
        store.setSource(""); ok("unconfigured".equals(store.check(true).getString("phase")), "blank source baseline available");
        ok("".equals(store(cache, fake).status().getString("source")), "blank source persists");
        rejects(() -> store.setSource("http://bad/current.json"), "invalid source rejected");
        ok("".equals(store.status().getString("source")), "invalid source preserves setting");
        Fake large = new Fake(); large.publish("9007199254740993"); large.manifest = new byte[16385];
        CatalogUpdates limited = store(directory("limits"), large);
        ok("CATALOG_TOO_LARGE".equals(limited.check(true).getString("error")), "manifest bound even with injected transport");
        large.publish("9007199254740993"); large.body = new byte[4 * 1024 * 1024 + 1];
        ok(!limited.check(true).getBoolean("updated"), "catalog bound");
        final CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        final Fake slow = new Fake(); slow.publish("9007199254740993");
        CatalogUpdates concurrent = new CatalogUpdates(directory("concurrent"), catalog("1"), SOURCE, (url, limit) -> {
            entered.countDown(); release.await(5, TimeUnit.SECONDS); return slow.get(url, limit);
        });
        concurrent.start(true); ok(entered.await(5, TimeUnit.SECONDS), "worker starts");
        concurrent.start(true); concurrent.check(true); ok(concurrent.status().getBoolean("checking"), "single worker nonblocking status");
        concurrent.setSource(""); release.countDown();
        for (int n = 0; n < 100 && concurrent.status().getBoolean("checking"); n++) Thread.sleep(20);
        ok("1".equals(concurrent.catalog().getString("version")), "source changed during download cannot activate");
        Fake repairFake = new Fake(); repairFake.publish("9007199254740993");
        File repairDir = directory("repair");CatalogUpdates repairStore = store(repairDir, repairFake);
        repairStore.check(true);String repairHash = repairStore.catalog().getString("_revision");
        Files.write(new File(repairDir,repairHash+".json").toPath(),new byte[]{1});
        CatalogUpdates repairReopened = store(repairDir,repairFake);
        ok(repairReopened.check(true).getBoolean("updated"),"verified download repairs corrupt cache blob");
        ok(repairHash.equals(store(repairDir,repairFake).catalog().getString("_revision")),"repaired cache survives restart");
        Fake observedFake = new Fake(); observedFake.publish("9007199254740993");
        CatalogUpdates observed = store(directory("observer"), observedFake);
        final List<String> observations = new ArrayList<>();
        observed.setObserver((data, source, force) -> observations.add(data.optString("version") + ":" + source + ":" + force));
        observed.check(true);
        ok(!observations.isEmpty(), "observer called after catalog check");
        ok(observations.get(observations.size()-1).equals("9007199254740993:" + SOURCE + ":true"), "observer receives verified active catalog");
        observed.setSource("");
        ok(observations.get(observations.size()-1).equals("9007199254740993::false"), "observer invalidates visual source when disabled");
        // A late network error belongs to A, even when B has not requested a check yet.
        final String replacementSource = "https://replacement.example/config/current.json";
        final CountDownLatch staleEntered = new CountDownLatch(1), staleRelease = new CountDownLatch(1);
        CatalogUpdates stale = new CatalogUpdates(directory("stale-status"), catalog("1"), SOURCE, (url, limit) -> {
            staleEntered.countDown(); staleRelease.await(5, TimeUnit.SECONDS); throw new IOException("old source offline");
        });
        stale.start(true); ok(staleEntered.await(5, TimeUnit.SECONDS), "stale worker reached transport");
        stale.setSource(replacementSource); staleRelease.countDown(); waitSettled(stale);
        ok(stale.status().getString("error").isEmpty() && stale.status().getLong("retry_at") == 0, "old source failure cannot back off or mark new source as failed");
        ok("idle".equals(stale.status().getString("phase")), "unrequested replacement source settles idle");

        final Fake replacement = new Fake(); replacement.publish("3");
        final CountDownLatch sourceAEntered = new CountDownLatch(1), sourceARelease = new CountDownLatch(1);
        final CountDownLatch sourceBEntered = new CountDownLatch(1), sourceBRelease = new CountDownLatch(1);
        CatalogUpdates switched = new CatalogUpdates(directory("queued-source"), catalog("1"), SOURCE, (url, limit) -> {
            if (url.equals(SOURCE)) { sourceAEntered.countDown(); sourceARelease.await(5, TimeUnit.SECONDS); throw new IOException("old source offline"); }
            if (url.equals(replacementSource)) { sourceBEntered.countDown(); sourceBRelease.await(5, TimeUnit.SECONDS); return replacement.manifest; }
            if (url.equals("https://replacement.example/config/catalog-3.json")) return replacement.body;
            throw new AssertionError("unexpected source-switch request " + url);
        });
        switched.start(true); ok(sourceAEntered.await(5, TimeUnit.SECONDS), "source A starts");
        switched.setSource(replacementSource); switched.start(true); sourceARelease.countDown();
        ok(sourceBEntered.await(5, TimeUnit.SECONDS), "source B starts without waiting for another user request");
        ok(switched.status().getBoolean("checking") && switched.status().getString("error").isEmpty() && switched.status().getLong("retry_at") == 0, "queued replacement remains checking without stale failure");
        sourceBRelease.countDown(); waitSettled(switched);
        ok("3".equals(switched.catalog().getString("version")) && "current".equals(switched.status().getString("phase")), "queued replacement catalog activates");

        // A forced synchronous check queued behind an async worker must survive a later non-forced start.
        final Fake initialVersion = new Fake(); initialVersion.publish("2");
        final CountDownLatch sameEntered = new CountDownLatch(1), sameRelease = new CountDownLatch(1);
        final java.util.concurrent.atomic.AtomicInteger manifestCalls = new java.util.concurrent.atomic.AtomicInteger();
        CatalogUpdates forced = new CatalogUpdates(directory("queued-force"), catalog("1"), SOURCE, (url, limit) -> {
            if (url.equals(SOURCE)) {
                if (manifestCalls.incrementAndGet() == 1) { sameEntered.countDown(); sameRelease.await(5, TimeUnit.SECONDS); return initialVersion.manifest; }
                return replacement.manifest;
            }
            if (url.endsWith("catalog-2.json")) return initialVersion.body;
            if (url.endsWith("catalog-3.json")) return replacement.body;
            throw new AssertionError("unexpected queued-force request " + url);
        });
        forced.start(true); ok(sameEntered.await(5, TimeUnit.SECONDS), "same-source worker starts");
        forced.start(false); forced.check(true); forced.start(false); sameRelease.countDown(); waitSettled(forced);
        ok("3".equals(forced.catalog().getString("version")) && manifestCalls.get() == 2, "queued requests coalesce and retain force across six-hour throttle");
        System.out.println("CatalogUpdates real Android JSON checks passed: " + assertions);
        System.exit(0);
    }

    static void waitSettled(CatalogUpdates updates) throws Exception {
        for (int i = 0; i < 500 && updates.status().getBoolean("checking"); i++) Thread.sleep(10);
        ok(!updates.status().getBoolean("checking"), "catalog worker settled");
    }

    static void validateShared(JSONObject shared) throws Exception {
        CatalogUpdates.validateCatalog(shared.getJSONObject("valid"), false);
        JSONArray catalogs = shared.getJSONArray("invalid_catalogs");
        for (int i = 0; i < catalogs.length(); i++) {
            final JSONObject invalid = catalogs.getJSONObject(i);
            rejects(() -> CatalogUpdates.validateCatalog(invalid, false), "shared invalid catalog");
        }
        JSONArray sources = shared.getJSONArray("invalid_sources");
        for (int i = 0; i < sources.length(); i++) {
            final String invalid = sources.getString(i);
            rejects(() -> CatalogUpdates.validateSource(invalid), "shared invalid source");
        }
    }
}
