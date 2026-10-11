// WebGL2 ray-marched volume viewer for 32^3 uint8 density frames.
// Approach after the three.js volume examples (webgl_texture3d / webgl_volume_cloud, MIT):
// render the back faces of a unit box, march a ray through a Data3DTexture in the
// fragment shader, composite front to back with an emission-absorption transfer function.
import * as THREE from "three";
import { OrbitControls } from "../vendor/OrbitControls.js";

const VERT = /* glsl */ `
varying vec3 vOrigin;
varying vec3 vDir;
void main() {
  vOrigin = (inverse(modelMatrix) * vec4(cameraPosition, 1.0)).xyz;
  vDir = position - vOrigin;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const FRAG = /* glsl */ `
precision highp float;
precision highp sampler3D;
varying vec3 vOrigin;
varying vec3 vDir;
uniform sampler3D uA;
uniform sampler3D uB;
uniform float uMix;
uniform float uInvMax;
uniform float uThr;
uniform float uOpacity;
uniform float uTexel;
uniform float uClipOn;
uniform vec3  uClipAxis;
uniform float uClipPos;
uniform float uClipFlip;
uniform vec3 uC0;
uniform vec3 uC1;
uniform vec3 uC2;
uniform vec3 uC3;

vec2 hitBox(vec3 o, vec3 d) {
  vec3 inv = 1.0 / d;
  vec3 a = (vec3(-0.5) - o) * inv;
  vec3 b = (vec3( 0.5) - o) * inv;
  vec3 tmin = min(a, b);
  vec3 tmax = max(a, b);
  return vec2(max(max(tmin.x, tmin.y), tmin.z), min(min(tmax.x, tmax.y), tmax.z));
}
float dens(vec3 p) {
  vec3 q = p + 0.5;
  return mix(texture(uA, q).r, texture(uB, q).r, uMix) * uInvMax;
}
vec3 ramp(float d) {
  d = clamp(d, 0.0, 1.0);
  vec3 c = mix(uC0, uC1, smoothstep(0.0, 0.35, d));
  c = mix(c, uC2, smoothstep(0.3, 0.7, d));
  return mix(c, uC3, smoothstep(0.7, 1.0, d));
}
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  vec3 rd = normalize(vDir);
  vec2 hit = hitBox(vOrigin, rd);
  if (hit.x > hit.y) discard;
  hit.x = max(hit.x, 0.0);
  const int N = 112;
  float dt = (hit.y - hit.x) / float(N);
  vec3 p = vOrigin + rd * (hit.x + dt * hash(gl_FragCoord.xy));
  vec4 acc = vec4(0.0);
  bool wasClipped = false;
  for (int i = 0; i < N; i++) {
    bool clipped = false;
    if (uClipOn > 0.5) {
      float side = (dot(p + 0.5, uClipAxis) - uClipPos) * uClipFlip;
      clipped = side < 0.0;
    }
    if (!clipped) {
      float d = dens(p);
      if (wasClipped) {
        // the cut face: show the density on the plane as an almost opaque slice
        float capA = 0.10 + 0.86 * smoothstep(0.0, 0.06, d);
        vec3 cc = ramp(d * 1.05) * (0.55 + 0.5 * d);
        acc.rgb += (1.0 - acc.a) * capA * cc;
        acc.a   += (1.0 - acc.a) * capA;
      }
      if (d > uThr) {
        float s = clamp((d - uThr) / max(1.0 - uThr, 0.05), 0.0, 1.0);
        float sigma = uOpacity * 26.0 * pow(s, 0.62);
        float a = 1.0 - exp(-sigma * dt);
        vec3 e = vec3(uTexel, 0.0, 0.0);
        vec3 g = vec3(dens(p + e.xyy) - dens(p - e.xyy),
                      dens(p + e.yxy) - dens(p - e.yxy),
                      dens(p + e.yyx) - dens(p - e.yyx));
        float gl = length(g);
        float lit = 0.62;
        if (gl > 1e-4) {
          vec3 n = -g / gl;
          lit = 0.5 + 0.5 * clamp(dot(n, -rd), 0.0, 1.0);
          lit += 0.25 * pow(1.0 - abs(dot(n, rd)), 3.0);
        }
        vec3 c = ramp(d) * lit;
        acc.rgb += (1.0 - acc.a) * a * c;
        acc.a   += (1.0 - acc.a) * a;
      }
    }
    wasClipped = clipped;
    if (acc.a > 0.985) break;
    p += rd * dt;
  }
  if (acc.a < 0.004) discard;
  gl_FragColor = vec4(acc.rgb, acc.a);   // premultiplied
}`;

export function webglSupported() {
  // ?gl=off is a test hook for the no-WebGL fallback
  try { if (new URLSearchParams(location.search).get("gl") === "off") return false; } catch (e) { /* ignore */ }
  try {
    const c = document.createElement("canvas");
    return !!c.getContext("webgl2");
  } catch (e) {
    return false;
  }
}

const RAMP = ["#6650c8", "#a862cc", "#f0759a", "#fff3f5"];

export class Viewer {
  /** @param {HTMLElement} host element that receives the canvas; @param {HTMLElement} eventEl element that gets pointer/key events */
  constructor(host, eventEl, { size = 32, onFrame } = {}) {
    this.host = host;
    this.size = size;
    this.onFrame = onFrame || (() => {});
    this.renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, premultipliedAlpha: true, powerPreference: "high-performance" });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.autoClear = false;
    host.appendChild(this.renderer.domElement);
    this.canvas = this.renderer.domElement;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1.6, 0.1, 20);
    this.home = new THREE.Vector3(1.55, 1.0, 1.55).multiplyScalar(0.98);
    this.camera.position.copy(this.home);

    this.controls = new OrbitControls(this.camera, eventEl);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.minDistance = 0.45;
    this.controls.maxDistance = 4.2;
    this.controls.enablePan = false;
    this.controls.rotateSpeed = 0.8;
    this.controls.autoRotateSpeed = 1.2;
    this.controls.addEventListener("change", () => this.invalidate());
    this.controls.target.set(0, 0, 0);

    const box = new THREE.BoxGeometry(1, 1, 1);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(box),
      new THREE.LineBasicMaterial({ color: 0xb79ad0, transparent: true, opacity: 0.3, depthTest: false }),
    );
    edges.renderOrder = 0;
    this.scene.add(edges);
    this.box = box;

    this.layers = [];
    this.opts = { invMax: 1, opacity: 1, thr: 0.03, clip: { on: false, axis: "z", pos: 0.5, flip: false } };
    this.dirty = true;
    this.running = false;
    this.contextLost = false;
    this._viewRects = [];

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); this.contextLost = true; this.onContext?.(true); });
    this.canvas.addEventListener("webglcontextrestored", () => { this.contextLost = false; this.invalidate(); this.onContext?.(false); });
    eventEl.addEventListener("keydown", (e) => this.onKey(e));
    this.eventEl = eventEl;
    this.loop = this.loop.bind(this);
    this.resize();
  }

  makeLayer() {
    const n = this.size;
    const mk = () => {
      const t = new THREE.Data3DTexture(new Uint8Array(n * n * n), n, n, n);
      t.format = THREE.RedFormat;
      t.type = THREE.UnsignedByteType;
      t.minFilter = t.magFilter = THREE.LinearFilter;
      t.wrapS = t.wrapT = t.wrapR = THREE.ClampToEdgeWrapping;
      t.unpackAlignment = 1;
      t.needsUpdate = true;
      return t;
    };
    const mat = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      side: THREE.BackSide,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      blendSrcAlpha: THREE.OneFactor,
      blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
      uniforms: {
        uA: { value: mk() }, uB: { value: mk() },
        uMix: { value: 0 }, uInvMax: { value: 1 }, uThr: { value: 0.04 }, uOpacity: { value: 1 },
        uTexel: { value: 0.75 / n },
        uClipOn: { value: 0 }, uClipAxis: { value: new THREE.Vector3(0, 0, 1) }, uClipPos: { value: 0.5 }, uClipFlip: { value: 1 },
        uC0: { value: new THREE.Color(RAMP[0]) }, uC1: { value: new THREE.Color(RAMP[1]) },
        uC2: { value: new THREE.Color(RAMP[2]) }, uC3: { value: new THREE.Color(RAMP[3]) },
      },
    });
    const mesh = new THREE.Mesh(this.box, mat);
    mesh.renderOrder = 1;
    this.scene.add(mesh);
    return { mesh, mat, aId: null, bId: null };
  }

  /** layers: [{aId, aData, bId, bData, mix}] (1 or 2 entries) or [] to show nothing. */
  setLayers(list) {
    while (this.layers.length < list.length) this.layers.push(this.makeLayer());
    this.layers.forEach((L, i) => { L.mesh.visible = i < list.length; });
    list.forEach((s, i) => {
      const L = this.layers[i];
      if (s.aId !== L.aId) { L.mat.uniforms.uA.value.image.data.set(s.aData); L.mat.uniforms.uA.value.needsUpdate = true; L.aId = s.aId; }
      if (s.bId !== L.bId) { L.mat.uniforms.uB.value.image.data.set(s.bData); L.mat.uniforms.uB.value.needsUpdate = true; L.bId = s.bId; }
      L.mat.uniforms.uMix.value = s.mix;
    });
    this.count = list.length;
    this.invalidate();
  }

  setOptions(o) {
    Object.assign(this.opts, o);
    if (o.clip) this.opts.clip = { ...this.opts.clip, ...o.clip };
    const c = this.opts.clip;
    const axis = c.axis === "x" ? [1, 0, 0] : c.axis === "y" ? [0, 1, 0] : [0, 0, 1];
    for (const L of this.layers) {
      const u = L.mat.uniforms;
      u.uInvMax.value = this.opts.invMax;
      u.uOpacity.value = this.opts.opacity;
      u.uThr.value = this.opts.thr;
      u.uClipOn.value = c.on ? 1 : 0;
      u.uClipAxis.value.set(axis[0], axis[1], axis[2]);
      u.uClipPos.value = c.pos;
      u.uClipFlip.value = c.flip ? -1 : 1;
    }
    this.invalidate();
  }

  setAutoRotate(on) { this.controls.autoRotate = !!on; this.invalidate(); }

  /** Frame a region of the box (object space, box is -0.5..0.5). Used by "Reset view". */
  setFocus(center, radius) {
    this.focus = { center: new THREE.Vector3(...center), radius };
    this.resetView();
  }

  resetView() {
    const dir = this.homeDir || (this.homeDir = this.home.clone().normalize());
    const f = this.focus;
    const target = f ? f.center : new THREE.Vector3(0, 0, 0);
    const dist = f ? Math.min(this.home.length(), Math.max(0.6, f.radius * 3.5)) : this.home.length();
    this.controls.target.copy(target);
    this.camera.position.copy(target).addScaledVector(dir, dist);
    this.controls.update();
    this.invalidate();
  }

  onKey(e) {
    const step = e.shiftKey ? 0.25 : 0.1;
    const off = this.camera.position.clone().sub(this.controls.target);
    const sph = new THREE.Spherical().setFromVector3(off);
    let used = true;
    switch (e.key) {
      case "ArrowLeft": sph.theta -= step; break;
      case "ArrowRight": sph.theta += step; break;
      case "ArrowUp": sph.phi = Math.max(0.12, sph.phi - step); break;
      case "ArrowDown": sph.phi = Math.min(Math.PI - 0.12, sph.phi + step); break;
      case "+": case "=": sph.radius = Math.max(this.controls.minDistance, sph.radius * 0.9); break;
      case "-": case "_": sph.radius = Math.min(this.controls.maxDistance, sph.radius * 1.1); break;
      case "r": case "R": this.resetView(); e.preventDefault(); return;
      default: used = false;
    }
    if (!used) return;
    e.preventDefault();
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(sph));
    this.controls.update();
    this.invalidate();
  }

  resize() {
    const w = Math.max(1, this.host.clientWidth), h = Math.max(1, this.host.clientHeight);
    const dpr = Math.min(window.devicePixelRatio || 1, w < 600 ? 1.5 : 2);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
    this.w = w; this.h = h;
    this.invalidate();
  }

  invalidate() {
    this.dirty = true;
    if (!this.running) { this.running = true; requestAnimationFrame(this.loop); }
  }

  loop() {
    this.running = false;
    if (this.contextLost) return;
    const moved = this.controls.update();
    if (this.dirty || moved) { this.draw(); this.dirty = false; }
    if (this.controls.autoRotate || moved) { this.running = true; requestAnimationFrame(this.loop); }
  }

  draw() {
    const r = this.renderer;
    const n = Math.max(1, this.count || 0);
    const w = this.w, h = this.h;
    r.setScissorTest(false);
    r.setViewport(0, 0, w, h);
    r.clear(true, true, true);
    r.setScissorTest(true);
    const aspect = (w / n) / h;
    this.camera.aspect = aspect;
    // keep the whole box in view when the viewport is narrow (phones, split view)
    const minH = THREE.MathUtils.degToRad(46);
    this.camera.fov = Math.max(32, THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(minH / 2) / aspect)));
    this.camera.updateProjectionMatrix();
    for (let i = 0; i < n; i++) {
      const x = Math.round((w / n) * i), vw = Math.round(w / n);
      r.setViewport(x, 0, vw, h);
      r.setScissor(x, 0, vw, h);
      this.layers.forEach((L, j) => { L.mesh.visible = j === i && j < (this.count || 0); });
      r.render(this.scene, this.camera);
    }
    this.layers.forEach((L, j) => { L.mesh.visible = j < (this.count || 0); });
    this.onFrame(this.camera);
  }

  dispose() {
    this.ro.disconnect();
    this.controls.dispose();
    this.layers.forEach((L) => { L.mat.uniforms.uA.value.dispose(); L.mat.uniforms.uB.value.dispose(); L.mat.dispose(); });
    this.renderer.dispose();
    this.canvas.remove();
  }
}
