using System;
using System.IO;
using System.Reflection;
using SFML.Graphics;

static class TextureCacheCheck {
    static void Check(bool ok,string message) {if(!ok)throw new Exception(message);}
    static void GraphicsChecks(string directory) {
        string a=Path.Combine(directory,"a","same.png"),b=Path.Combine(directory,"b","same.png");
        using(var image=new Image(4,4,new Color(17,59,103,191))) {Check(image.SaveToFile(a),"fixture save failed");Check(image.SaveToFile(b),"fixture copy save failed");}
        using var loader=new SceneTextureLoader();
        Type[] pages={typeof(SpineRuntime21.AtlasPage),typeof(SpineRuntime34.AtlasPage),typeof(SpineRuntime35.AtlasPage),
            typeof(SpineRuntime36.AtlasPage),typeof(SpineRuntime37.AtlasPage),typeof(SpineRuntime38.AtlasPage),
            typeof(SpineRuntime40.AtlasPage),typeof(SpineRuntime41.AtlasPage),typeof(SpineRuntime42.AtlasPage)};
        Texture? shared=null,original38=null;
        foreach(var type in pages) {
            object Page(string filter) {
                var page=Activator.CreateInstance(type)!;
                foreach(string name in new[]{"minFilter","magFilter"}) {var field=type.GetField(name)!;field.SetValue(page,Enum.Parse(field.FieldType,filter));}
                type.GetField("height")!.SetValue(page,99);return page;
            }
            var load=typeof(SceneTextureLoader).GetMethod("Load",new[]{type,typeof(string)})!;
            var first=Page("Linear");var second=Page("Linear");
            load.Invoke(loader,new[]{first,(object)a});load.Invoke(loader,new[]{second,(object)b});
            var texture=(Texture)type.GetField("rendererObject")!.GetValue(first)!;
            Check(ReferenceEquals(texture,type.GetField("rendererObject")!.GetValue(second)),"same bytes/sampler not shared: "+type);
            Check((int)type.GetField("width")!.GetValue(second)! ==4&&(int)type.GetField("height")!.GetValue(second)! ==4,"dimension fallback differs");
            if(shared!=null)Check(!ReferenceEquals(shared,texture),"runtime identities collided");
            shared=texture;loader.Unload(texture);Check(texture.Size.X==4,"unload invalidated another page");
            if(type==typeof(SpineRuntime38.AtlasPage))original38=texture;
            var nearest=Page("Nearest");load.Invoke(loader,new[]{nearest,(object)b});
            Check(!ReferenceEquals(texture,type.GetField("rendererObject")!.GetValue(nearest)),"different sampler shared");
            var wrap=Page("Linear");var wrapField=type.GetField("uWrap")!;wrapField.SetValue(wrap,Enum.Parse(wrapField.FieldType,"Repeat"));
            load.Invoke(loader,new[]{wrap,(object)b});Check(!ReferenceEquals(texture,type.GetField("rendererObject")!.GetValue(wrap)),"different wrap shared");
            var pmaField=type.GetField("pma");
            if(pmaField!=null) {var pma=Page("Linear");pmaField.SetValue(pma,true);load.Invoke(loader,new[]{pma,(object)b});Check(!ReferenceEquals(texture,type.GetField("rendererObject")!.GetValue(pma)),"different atlas PMA shared");}
        }
        using(var image=new Image(4,4,new Color(211,29,71,137)))Check(image.SaveToFile(b),"replacement save failed");
        var changed=new SpineRuntime38.AtlasPage{minFilter=SpineRuntime38.TextureFilter.Linear,magFilter=SpineRuntime38.TextureFilter.Linear};
        loader.Load(changed,b);
        Check(!ReferenceEquals(original38,changed.rendererObject),"changed contents reused");
        loader.Dispose();Check(shared!.IsInvalid,"scene owner did not release shared texture");loader.Dispose();
        Console.WriteLine("PASS 9 runtimes GPU sharing/sampling/dimensions/deferred ownership");
    }
    static int Main(string[] args) {
        var type=typeof(FloatCanvas).Assembly.GetType("SceneTextureLoader");
        Check(type!=null,"scene-owned texture cache is missing");
        var loader=Activator.CreateInstance(type!)!;
        using var owner=(IDisposable)loader;
        var method=type!.GetMethod("ContentKey",BindingFlags.Instance|BindingFlags.NonPublic)!;
        Check(method!=null,"content identity API missing");
        string Key(string path,string policy) => (string)method!.Invoke(loader,new object[]{path,policy})!;
        Directory.CreateDirectory(Path.Combine(args[0],"a"));Directory.CreateDirectory(Path.Combine(args[0],"b"));
        string a=Path.Combine(args[0],"a","same.png"),b=Path.Combine(args[0],"b","same.png");
        File.WriteAllBytes(a,new byte[]{1,2,3,4});File.WriteAllBytes(b,new byte[]{1,2,3,4});
        Check(Key(a,"38/linear") == Key(b,"38/linear"),"same bytes in different directories did not share identity");
        File.WriteAllBytes(b,new byte[]{1,2,3,5});
        Check(Key(a,"38/linear") != Key(b,"38/linear"),"same basename with different bytes collided");
        Check(Key(a,"38/linear") != Key(a,"42/linear"),"runtime sampling policy ignored");
        Check(Key(a,"38/linear") != Key(a,"38/nearest"),"filter policy ignored");
        foreach(string flag in new[]{"ForcePremul","ForceNearest","ForceMipmap"}) {
            var property=type.GetProperty(flag)!;bool before=(bool)property.GetValue(loader)!;
            string original=Key(a,"38/linear");property.SetValue(loader,!before);
            Check(original!=Key(a,"38/linear"),flag+" ignored");property.SetValue(loader,before);
        }
        try {Key(Path.Combine(args[0],"missing.png"),"38/linear");throw new Exception("missing source accepted");}
        catch(TargetInvocationException error) when(error.InnerException is FileNotFoundException) {}
        Console.WriteLine("PASS exact content/sampling/flags texture identities");
        if(args.Length>1&&args[1]=="--graphics")GraphicsChecks(args[0]);return 0;
    }
}
