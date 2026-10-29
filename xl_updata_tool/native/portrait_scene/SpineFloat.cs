// Slot-level source capture preserves actual Spine blend factors against the float destination.
using System;
using System.Linq;
using System.Threading;
using SFML.Graphics;
using SFML.System;
using Spine.Interfaces;
using Spine.Interfaces.Attachments;
using Spine.Utils;

sealed partial class FloatCanvas {
    static float Factor(BlendMode.Factor factor,float src,float dst,float sa,float da) => factor switch {
        BlendMode.Factor.Zero=>0,BlendMode.Factor.One=>1,
        BlendMode.Factor.SrcColor=>src,BlendMode.Factor.OneMinusSrcColor=>1-src,
        BlendMode.Factor.DstColor=>dst,BlendMode.Factor.OneMinusDstColor=>1-dst,
        BlendMode.Factor.SrcAlpha=>sa,BlendMode.Factor.OneMinusSrcAlpha=>1-sa,
        BlendMode.Factor.DstAlpha=>da,BlendMode.Factor.OneMinusDstAlpha=>1-da,
        _=>throw new ArgumentException("Unsupported Spine blend factor")
    };
    static float Equation(BlendMode.Equation equation,float src,float dst,float s,float d) => equation switch {
        BlendMode.Equation.Add=>s+d,BlendMode.Equation.Subtract=>s-d,
        BlendMode.Equation.ReverseSubtract=>d-s,BlendMode.Equation.Min=>Math.Min(src,dst),
        BlendMode.Equation.Max=>Math.Max(src,dst),_=>throw new ArgumentException("Unsupported Spine blend equation")
    };
    public void CaptureBlend(byte[] bytes, BlendMode mode,int x0=0,int y0=0,int x1=-1,int y1=-1) {
        x1=x1<0?Width:Math.Min(Width,x1);y1=y1<0?Height:Math.Min(Height,y1);
        int standard=mode==SFMLBlendMode.NormalPma?1:mode==SFMLBlendMode.AdditivePma?2:mode==SFMLBlendMode.MultiplyPma?3:mode==SFMLBlendMode.ScreenPma?4:0;
        for(int y=Math.Max(0,y0);y<y1;y++)for(int i=(y*Width+Math.Max(0,x0))*4,end=(y*Width+x1)*4;i<end;i+=4) {
            // Only all-zero RGBA is identity. PMA additive RGB with alpha0 is meaningful.
            if(standard!=0&&(bytes[i]|bytes[i+1]|bytes[i+2]|bytes[i+3])==0)continue;
            float sa=bytes[i+3]/255f,da=Pixels[i+3];
            for(int c=0;c<3;c++) {
                float src=bytes[i+c]/255f,dst=Pixels[i+c];
                Pixels[i+c]=standard switch {
                    1=>src+dst*(1-sa),2=>src+dst,3=>src*dst+dst*(1-sa),4=>src+dst*(1-src),
                    _=>Equation(mode.ColorEquation,src,dst,src*Factor(mode.ColorSrcFactor,src,dst,sa,da),dst*Factor(mode.ColorDstFactor,src,dst,sa,da))
                };
            }
            Pixels[i+3]=Math.Clamp(Equation(mode.AlphaEquation,sa,da,sa*Factor(mode.AlphaSrcFactor,sa,da,sa,da),da*Factor(mode.AlphaDstFactor,sa,da,sa,da)),0,1);
            Peak=Math.Max(Peak,Math.Max(Pixels[i],Math.Max(Pixels[i+1],Pixels[i+2])));
        }
    }
}
sealed partial class ScenePart {
    public void DrawFloat(RenderTexture target,FloatCanvas canvas,CancellationToken token) {
        // Normal-only parts can remain one capture. Non-normal slots need their
        // own source image because their destination is the existing scene.
        if(Skeleton.IterDrawOrder().All(slot=>slot.Blend==SFMLBlendMode.NormalPma)) {
            target.Clear(Color.Transparent);Draw(target,RenderStates.Default);target.Display();
            using var image=target.Texture.CopyToImage();canvas.Capture(image.Pixels);return;
        }
        using var captureView=target.GetView();
        float projectionX=target.Size.X/captureView.Size.X,projectionY=target.Size.Y/captureView.Size.Y;
        var clipping=((SpineObjectData)Data).CreateSkeletonClipping();
        float alpha=Skeleton.A;Skeleton.A*=Fade;
        float[] world=Array.Empty<float>();
        bool normalPending=false;
        void FlushNormal() {
            if(!normalPending)return;
            target.Display();
            using var image=target.Texture.CopyToImage();canvas.Capture(image.Pixels);
            normalPending=false;
        }
        try {
            foreach(var slot in Skeleton.IterDrawOrder()) {
                token.ThrowIfCancellationRequested();
                if(slot.A<=0||!slot.Bone.Active||slot.Disabled){clipping.ClipEnd(slot);continue;}
                var attachment=slot.Attachment;
                Texture texture;float[] uvs;int[] triangles;
                float r=Skeleton.R*slot.R,g=Skeleton.G*slot.G,b=Skeleton.B*slot.B,a=Skeleton.A*slot.A;
                if(attachment is IRegionAttachment region){texture=region.RendererObject;uvs=region.UVs;triangles=region.Triangles;r*=region.R;g*=region.G;b*=region.B;a*=region.A;}
                else if(attachment is IMeshAttachment mesh){texture=mesh.RendererObject;uvs=mesh.UVs;triangles=mesh.Triangles;r*=mesh.R;g*=mesh.G;b*=mesh.B;a*=mesh.A;}
                else if(attachment is IClippingAttachment clip){clipping.ClipStart(slot,clip);continue;}
                else{clipping.ClipEnd(slot);continue;}
                int length=attachment.ComputeWorldVertices(slot,ref world),count=triangles.Length;
                float[] vertices=world;
                if(clipping.IsClipping){clipping.ClipTriangles(world,length,triangles,count,uvs);vertices=clipping.ClippedVertices;uvs=clipping.ClippedUVs;triangles=clipping.ClippedTriangles;count=clipping.ClippedTrianglesLength;}
                var draw=new Vertex[count];
                float minX=float.PositiveInfinity,minY=float.PositiveInfinity,maxX=float.NegativeInfinity,maxY=float.NegativeInfinity;
                var color=new Color((byte)(r*255),(byte)(g*255),(byte)(b*255),(byte)(a*255));
                for(int j=0;j<count;j++){
                    int k=triangles[j]*2;var position=affine.TransformPoint(vertices[k],vertices[k+1]);
                    draw[j]=new Vertex(position,color,new Vector2f(uvs[k]*texture.Size.X,uvs[k+1]*texture.Size.Y));
                    float px=(position.X-captureView.Center.X)*projectionX+target.Size.X/2f,py=(position.Y-captureView.Center.Y)*projectionY+target.Size.Y/2f;
                    minX=Math.Min(minX,px);maxX=Math.Max(maxX,px);minY=Math.Min(minY,py);maxY=Math.Max(maxY,py);
                }
                clipping.ClipEnd(slot);
                bool normal=slot.Blend==SFMLBlendMode.NormalPma;
                if(!normal)FlushNormal();
                if(!normalPending)target.Clear(Color.Transparent);
                var states=RenderStates.Default;states.Texture=texture;states.BlendMode=SFMLBlendMode.NormalPma;states.Shader=UsePma?SFMLShader.VertexAlphaPma:SFMLShader.VertexAlpha;
                target.Draw(draw,PrimitiveType.Triangles,states);
                if(normal)normalPending=true;
                else {
                    target.Display();
                    using var image=target.Texture.CopyToImage();canvas.CaptureBlend(image.Pixels,slot.Blend,
                        (int)MathF.Floor(minX)-1,(int)MathF.Floor(minY)-1,(int)MathF.Ceiling(maxX)+1,(int)MathF.Ceiling(maxY)+1);
                }
            }
            FlushNormal();
        } finally {Skeleton.A=alpha;clipping.ClipEnd();}
    }
}
