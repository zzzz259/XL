using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using SFML.Graphics;
using Spine.Implementations;

// One export owns these textures until every referring atlas has been released.
// Only content and complete sampling policy can identify a reusable upload.
sealed class SceneTextureLoader : TextureLoader,IDisposable {
    readonly Dictionary<string,Texture> cache=new();
    readonly HashSet<Texture> owned=new(ReferenceEqualityComparer.Instance);
    bool disposed;
    public SceneTextureLoader() {
        ForcePremul=DefaultLoader.ForcePremul;ForceNearest=DefaultLoader.ForceNearest;ForceMipmap=DefaultLoader.ForceMipmap;
    }
    static string Policy(string runtime,object format,object min,object mag,object u,object v,bool? pma) =>
        string.Join("/",runtime,Convert.ToInt32(format,CultureInfo.InvariantCulture),Convert.ToInt32(min,CultureInfo.InvariantCulture),
            Convert.ToInt32(mag,CultureInfo.InvariantCulture),Convert.ToInt32(u,CultureInfo.InvariantCulture),
            Convert.ToInt32(v,CultureInfo.InvariantCulture),pma.HasValue?(pma.Value?"1":"0"):"-");
    internal string ContentKey(string path,string policy) {
        using var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read,65536,FileOptions.SequentialScan);
        long length=stream.Length;
        string digest=Convert.ToHexString(SHA256.HashData(stream));
        if(stream.Length!=length)throw new IOException("Texture source changed while hashing");
        return string.Join(";",policy,ForcePremul,ForceNearest,ForceMipmap,length.ToString(CultureInfo.InvariantCulture),digest);
    }
    Texture Share(string path,string policy,Action load,Func<Texture?> current) {
        if(disposed)throw new ObjectDisposedException(nameof(SceneTextureLoader));
        string key=ContentKey(path,policy);
        if(cache.TryGetValue(key,out var existing))return existing;
        var before=current();
        try {
            load();
            var texture=current()??throw new IOException("Texture loader returned no texture");
            owned.Add(texture);
            // The package is a trusted immutable input. Detect ordinary source
            // replacement rather than registering different pixels under a stale key.
            if(key!=ContentKey(path,policy))throw new IOException("Texture source changed during upload");
            cache.Add(key,texture);return texture;
        } catch {
            var exposed=current();
            if(exposed!=null&&!ReferenceEquals(before,exposed))owned.Add(exposed);
            throw;
        }
    }
    public override void Load(SpineRuntime21.AtlasPage page,string path) {
        var texture=Share(path,Policy("21",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,null),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime34.AtlasPage page,string path) {
        var texture=Share(path,Policy("34",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,null),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime35.AtlasPage page,string path) {
        var texture=Share(path,Policy("35",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,null),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime36.AtlasPage page,string path) {
        var texture=Share(path,Policy("36",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,null),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime37.AtlasPage page,string path) {
        var texture=Share(path,Policy("37",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,null),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime38.AtlasPage page,string path) {
        var texture=Share(path,Policy("38",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,null),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime40.AtlasPage page,string path) {
        var texture=Share(path,Policy("40",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,page.pma),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime41.AtlasPage page,string path) {
        var texture=Share(path,Policy("41",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,page.pma),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Load(SpineRuntime42.AtlasPage page,string path) {
        var texture=Share(path,Policy("42",page.format,page.minFilter,page.magFilter,page.uWrap,page.vWrap,page.pma),
            ()=>base.Load(page,path),()=>page.rendererObject as Texture);
        page.rendererObject=texture;
        if(page.width<=0||page.height<=0){page.width=(int)texture.Size.X;page.height=(int)texture.Size.Y;}
    }
    public override void Unload(object texture) {
        if(texture is Texture known&&owned.Contains(known))return;
        base.Unload(texture);
    }
    public void Dispose() {
        if(disposed)return;disposed=true;
        foreach(var texture in owned)texture.Dispose();
        cache.Clear();
    }
}

