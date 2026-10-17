import * as THREE from "three";
import {EffectComposer} from "three/examples/jsm/postprocessing/EffectComposer.js";
import {RenderPass} from "three/examples/jsm/postprocessing/RenderPass.js";
import {OutputPass} from "three/examples/jsm/postprocessing/OutputPass.js";
import {ShaderPass} from "three/examples/jsm/postprocessing/ShaderPass.js";
export type RenderFilters={exposure:number;contrast:number;saturation:number;warmth:number;vignette:number;clarity:number};
export const DEFAULT_FILTERS:RenderFilters={exposure:0,contrast:1,saturation:1,warmth:0,vignette:0,clarity:0};
export const FILTER_PRESETS:Record<string,RenderFilters>={
  "Нейтральный":{...DEFAULT_FILTERS},
  "Естественный":{exposure:.15,contrast:1.06,saturation:.92,warmth:.03,vignette:.12,clarity:.12},
  "Тёплый вечер":{exposure:.1,contrast:1.12,saturation:.95,warmth:.16,vignette:.22,clarity:.12},
  "Прохладный":{exposure:.1,contrast:1.08,saturation:.82,warmth:-.12,vignette:.12,clarity:.15},
};
/** A real render pass, shared by the viewport and downloaded PNG. CAD 2D
 * bypasses it so source drawing colors are never color-graded. */
export class RenderFilterPipeline {
  composer:EffectComposer;
  private grade:ShaderPass;
  constructor(renderer:THREE.WebGLRenderer,scene:THREE.Scene,camera:THREE.Camera) {
    this.composer=new EffectComposer(renderer);
    this.composer.addPass(new RenderPass(scene,camera));this.composer.addPass(new OutputPass());
    this.grade=new ShaderPass({
      uniforms:{tDiffuse:{value:null},pixel:{value:new THREE.Vector2(1,1)},contrast:{value:1},saturation:{value:1},warmth:{value:0},vignette:{value:0},clarity:{value:0}},
      vertexShader:`varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
      fragmentShader:`uniform sampler2D tDiffuse;uniform vec2 pixel;uniform float contrast,saturation,warmth,vignette,clarity;varying vec2 vUv;
      void main(){vec4 source=texture2D(tDiffuse,vUv);vec3 c=source.rgb;
      vec3 around=(texture2D(tDiffuse,vUv+vec2(pixel.x,0.)).rgb+texture2D(tDiffuse,vUv-vec2(pixel.x,0.)).rgb+texture2D(tDiffuse,vUv+vec2(0.,pixel.y)).rgb+texture2D(tDiffuse,vUv-vec2(0.,pixel.y)).rgb)*.25;
      c+=clamp(c-around,vec3(-.12),vec3(.12))*clarity;
      float l=dot(c,vec3(.2126,.7152,.0722));c=mix(vec3(l),c,saturation);c=(c-.5)*contrast+.5;
      c*=vec3(1.+warmth*.35,1.+warmth*.06,1.-warmth*.35);
      float edge=smoothstep(.18,.73,length(vUv-.5));c*=1.-vignette*edge;
      gl_FragColor=vec4(clamp(c,0.,1.),source.a);}`,
    });this.grade.material.toneMapped=false;this.composer.addPass(this.grade);
  }
  configure(filters:RenderFilters) {for(const key of ['contrast','saturation','warmth','vignette','clarity'] as const)this.grade.uniforms[key].value=filters[key];}
  resize(width:number,height:number,pixelRatio:number) {this.composer.setPixelRatio(pixelRatio);this.composer.setSize(width,height);this.grade.uniforms.pixel.value.set(1/(width*pixelRatio),1/(height*pixelRatio));}
  render(){this.composer.render();}
  dispose(){for(const pass of this.composer.passes)pass.dispose();this.composer.dispose();}
}
