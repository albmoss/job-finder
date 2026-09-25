import React, { useEffect, useRef } from 'react';
import FINGERPRINT from './fingerprint_shape.json';

/* Strumień: kształt lejka (7 wysokości etapów) wypełniany kobalt → fiolet do `progress`.
   Fale to pojedyncze impulsy, które przechodzą od lewej i ściskają kształt — nie falowanie
   całości. Impuls wypuszcza `pulse` (zdarzenie, np. paczka) albo `loop` (ciągły ruch jak
   u_time/u_period/u_waves/u_running w ../design/wave_stream.glsl z pen.dev). */

/* Kształt odcisku oferty (karta dopasowania) siedzi w fingerprint_shape.json. Najwygodniej
   stroić go w laboratorium: `npm run dev` → http://localhost:5173/fingerprint_lab.html. */
export type FingerprintShape = typeof FINGERPRINT;
const glf = (n: number) => n.toFixed(3);

const buildFrag = (fp: FingerprintShape) => `
precision highp float;
uniform vec2 u_res;
uniform float u_progress, u_depth, u_width, u_seed, u_dpr, u_edge;
uniform vec4 u_ha, u_hb;
uniform vec3 u_track, u_start, u_end, u_hot;
uniform float u_wp[6];
uniform float u_wa[6];
uniform float u_ws[6];
float hash(float n) { return fract(sin(n * 12.9898 + 3.17) * 43758.5453); }
float stageH(float i) {
  if (i < 0.5) return u_ha.x; if (i < 1.5) return u_ha.y; if (i < 2.5) return u_ha.z;
  if (i < 3.5) return u_ha.w; if (i < 4.5) return u_hb.x; if (i < 5.5) return u_hb.y;
  return u_hb.z;
}
// Odcisk oferty: jak ścieżka audio, lustrzany względem osi. Szum gradientowy (bez płaskich
// półek i prostych odcinków szumu wartości) w trzech oktawach pod wolną obwiednią „głośności”.
// Dopasowanie steruje charakterem: wyższy % = grubszy i bardziej rozedrgany płomień.
float gnoise(float x, float s) {
  float i = floor(x); float f = x - i;
  float u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  float g0 = hash(s + i) * 2.0 - 1.0;
  float g1 = hash(s + i + 1.0) * 2.0 - 1.0;
  return mix(g0 * f, g1 * (f - 1.0), u) * 2.0;
}
float seedEdge(float x, float s, float m) {
  float k = smoothstep(${glf(fp.matchFrom)}, ${glf(fp.matchTo)}, m);
  float body = mix(${glf(fp.bodyFrom)}, ${glf(fp.bodyTo)}, k);
  float fm = mix(${glf(fp.hillsFrom)}, ${glf(fp.hillsTo)}, k);
  float vol = 1.0 + ${glf(fp.swell)} * gnoise(x * 2.6, s + 151.0);
  float d = 0.66 * gnoise(x * 7.0 * fm, s) + 0.27 * gnoise(x * 16.0 * fm, s + 37.0)
          + 0.07 * gnoise(x * 34.0 * fm, s + 83.0);
  float ends = clamp(min(x, 1.0 - x) * 12.0, 0.0, 1.0);
  float e = body * vol * (1.0 + mix(${glf(fp.peaksFrom)}, ${glf(fp.peaksTo)}, k) * d);
  return clamp(max(e, ${glf(fp.minThick)}) * sqrt(ends), 0.04, ${glf(fp.maxThick)});
}
float profile(float x) {
  float f = clamp(x * 7.0 - 0.5, 0.0, 6.0);
  float i = floor(f);
  float s = smoothstep(0.25, 0.75, f - i);
  return mix(stageH(i), stageH(min(i + 1.0, 6.0)), s);
}
void main() {
  vec2 px = gl_FragCoord.xy;
  float W = u_res.x; float H = u_res.y;
  float head = u_progress * W;
  float lit = 1.0 - smoothstep(head - 0.8 * u_dpr, head + 0.8 * u_dpr, px.x);
  float sq = 0.0; float glow = 0.0;
  for (int i = 0; i < 6; i++) {
    float a = u_wa[i];
    if (a > 0.0) {
      float xp = u_wp[i] * head;
      float d = (px.x - xp) / (u_width * u_ws[i] * u_dpr);
      float g = exp(-0.5 * d * d);
      sq += a * (1.0 - d * d) * g;
      glow += a * g;
    }
  }
  sq *= lit; glow *= lit;
  float x = px.x / W;
  float dy = px.y - H * 0.5;
  float ady = abs(dy);
  // Ziarno: krawędź jak ścieżka audio, lustrzana względem osi; bez ziarna: lejek etapów.
  float s = mod(floor(u_seed), 251.0) * 17.0;
  float prof = u_seed > 0.5 ? seedEdge(x, s, u_progress) : profile(x);
  float base = prof * 0.92 * H * 0.5;
  // Fala ściska kształt najwyżej do 55% — nigdy do zera, żeby nic nie znikało w przejściu.
  float hh = min(max(base * (1.0 - u_depth * sq), max(base * 0.55, 1.2 * u_dpr)), H * 0.5);
  float inside = clamp((hh - ady) / u_dpr + 0.5, 0.0, 1.0);
  float v = clamp(ady / hh, 0.0, 1.0);
  float along = clamp(px.x / max(head, 1.0), 0.0, 1.0);
  vec3 c = mix(u_track * 1.25, u_start, smoothstep(0.0, 0.55, along));
  c = mix(c, u_end, smoothstep(0.45, 1.0, along));
  c *= 0.86 + 0.14 * (1.0 - v * v);
  c = mix(c, u_hot, clamp(glow * 0.45 * (0.4 + 0.6 * along), 0.0, 0.7));
  float dh = (px.x - head) / u_dpr;
  c = mix(c, u_hot, exp(-dh * dh / 4.0) * 0.8 * u_edge);
  vec3 wait = u_track * (0.9 + 0.1 * (1.0 - v));
  vec3 col = mix(wait, c, lit);
  gl_FragColor = vec4(col * inside, inside);
}`;
const FRAG = buildFrag(FINGERPRINT);

function cssColor(name: string, fallback: string): [number, number, number] {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const hex = raw.startsWith('#') ? raw.slice(1) : fallback.slice(1);
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
}

interface Wave {
  t0: number;
  amp: number;
  /** Czas przejścia w ms. */
  cross: number;
  /** Mnożnik szerokości impulsu. */
  width: number;
}

/** Samoczynne fale: pojedyncze impulsy w losowych odstępach (`gap`, sekundy), bez rytmu.
 *  `period` i `running` jak u_period/u_running w ../design/wave_stream.glsl: przejście trwa
 *  period / running s, a `running` ścisza impuls; szerokość rośnie z losową amplitudą. */
export interface StreamLoop {
  period: number;
  running: number;
  gap: [number, number];
}

interface StreamOptions {
  depth: number;
  width: number;
  cross: number;
  seed: number;
  edge: number;
  ha: [number, number, number, number];
  hb: [number, number, number, number];
  loop: StreamLoop | null;
}

class StreamRenderer {
  private gl: WebGLRenderingContext;
  private loc: (n: string) => WebGLUniformLocation | null;
  private waves: Wave[] = [];
  private raf = 0;
  private last = 0;
  private dpr = 1;
  private nextLoopAt = 0;
  private readonly reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  progress: number;
  target: number;
  private colors: Record<'track' | 'start' | 'end' | 'hot', [number, number, number]>;

  private prog: WebGLProgram;

  constructor(private cv: HTMLCanvasElement, public o: StreamOptions, progress: number, frag: string) {
    this.progress = progress;
    this.target = progress;
    // Tokeny czytane raz — getComputedStyle w każdej klatce wymusza przeliczenie stylów.
    this.colors = {
      track: cssColor('--track', '#2A2A2D'),
      start: cssColor('--accent-2', '#4B6BFF'),
      end: cssColor('--stream-end', '#8B5CFF'),
      hot: cssColor('--accent-hot', '#F1ECFF'),
    };
    const gl = cv.getContext('webgl', { premultipliedAlpha: true, antialias: true });
    if (!gl) throw new Error('WebGL niedostępny');
    this.gl = gl;
    const sh = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      return s;
    };
    const p = (this.prog = gl.createProgram()!);
    gl.attachShader(p, sh(gl.VERTEX_SHADER, 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}'));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, frag));
    gl.linkProgram(p);
    gl.useProgram(p);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const a = gl.getAttribLocation(p, 'a');
    gl.enableVertexAttribArray(a);
    gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
    this.loc = (n) => gl.getUniformLocation(p, n);
    this.resize();
    if (o.loop) this.wake();
  }

  resize() {
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(this.cv.clientWidth * this.dpr);
    const h = Math.round(this.cv.clientHeight * this.dpr);
    // Przypisanie width/height czyści bufor nawet przy tej samej wartości — tylko przy zmianie,
    // inaczej każdy sygnał z ResizeObserver daje jedną pustą klatkę (mignięcie).
    if (w < 1 || h < 1) return;
    if (this.cv.width !== w || this.cv.height !== h) {
      this.cv.width = w;
      this.cv.height = h;
    }
    this.draw();
  }

  emit(amp?: number) {
    if (this.reduce.matches) return;
    this.push({ t0: performance.now(), amp: amp ?? 0.6 + 0.4 * Math.random(), cross: this.o.cross * 1000, width: 1 });
    this.wake();
  }

  private push(w: Wave) {
    this.waves.push(w);
    if (this.waves.length > 6) this.waves.shift();
  }

  setTarget(t: number) {
    this.target = t;
    if (this.reduce.matches) this.progress = t;
    this.wake();
  }

  /** Włącza lub gasi samoczynne fale (arkusz pipeline'u: tylko w trakcie przebiegu).
   *  Trwające fale dobiegają do końca; pierwsza nowa rusza od razu. */
  setLoop(loop: StreamLoop | null) {
    this.o.loop = loop;
    this.nextLoopAt = 0;
    if (loop) this.wake();
  }

  private looping() {
    return this.o.loop !== null && !this.reduce.matches;
  }

  private wake() {
    if (this.raf) return;
    this.last = performance.now();
    this.raf = requestAnimationFrame(this.loop);
  }

  private loop = (now: number) => {
    const dt = Math.min(64, now - this.last);
    this.last = now;
    this.progress += (this.target - this.progress) * (1 - Math.exp(-dt / 140));
    const lp = this.o.loop;
    if (lp && this.looping() && now >= this.nextLoopAt) {
      // Pierwsza fala rusza przy otwarciu, kolejne po losowej przerwie — nigdy w stałym rytmie.
      const amp = 0.55 + 0.45 * Math.random();
      this.push({ t0: now, amp: lp.running * amp, cross: (lp.period / lp.running) * 1000, width: 0.75 + 0.35 * amp });
      this.nextLoopAt = now + (lp.gap[0] + Math.random() * (lp.gap[1] - lp.gap[0])) * 1000;
    }
    this.waves = this.waves.filter((w) => (now - w.t0) / w.cross < 1);
    this.draw(now);
    const busy = this.looping() || this.waves.length > 0 || Math.abs(this.target - this.progress) > 0.0005;
    this.raf = busy ? requestAnimationFrame(this.loop) : 0;
  };

  draw(now = performance.now()) {
    const { gl, o } = this;
    gl.viewport(0, 0, this.cv.width, this.cv.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const wp = new Float32Array(6);
    const wa = new Float32Array(6);
    const ws = new Float32Array(6);
    const sm = (a: number, b: number, x: number) => {
      const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
      return t * t * (3 - 2 * t);
    };
    this.waves.forEach((w, i) => {
      const ph = (now - w.t0) / w.cross;
      wp[i] = ph;
      wa[i] = w.amp * sm(0, 0.1, ph) * (1 - sm(0.85, 1, ph));
      ws[i] = w.width;
    });
    gl.uniform2f(this.loc('u_res'), this.cv.width, this.cv.height);
    gl.uniform1f(this.loc('u_progress'), this.progress);
    gl.uniform1f(this.loc('u_depth'), o.depth);
    gl.uniform1f(this.loc('u_width'), o.width);
    gl.uniform1f(this.loc('u_seed'), o.seed);
    gl.uniform1f(this.loc('u_dpr'), this.dpr);
    gl.uniform1f(this.loc('u_edge'), o.edge);
    gl.uniform4f(this.loc('u_ha'), ...o.ha);
    gl.uniform4f(this.loc('u_hb'), ...o.hb);
    gl.uniform3f(this.loc('u_track'), ...this.colors.track);
    gl.uniform3f(this.loc('u_start'), ...this.colors.start);
    gl.uniform3f(this.loc('u_end'), ...this.colors.end);
    gl.uniform3f(this.loc('u_hot'), ...this.colors.hot);
    gl.uniform1fv(this.loc('u_wp[0]'), wp);
    gl.uniform1fv(this.loc('u_wa[0]'), wa);
    gl.uniform1fv(this.loc('u_ws[0]'), ws);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.gl.deleteProgram(this.prog);
  }

  /** Oddaje kontekst WebGL — przeglądarka trzyma ich ~16 i gasi najstarsze (np. pasek). */
  release() {
    this.gl.getExtension('WEBGL_lose_context')?.loseContext();
  }
}

export interface StreamProps {
  /** 0–1: dokąd strumień jest wypełniony kolorem. */
  progress: number;
  /** Zmiana wartości wypuszcza jedną falę (np. numer zakończonej paczki). */
  pulse?: number | string;
  /** Siła fali wypuszczonej przez `pulse`; domyślnie losowa 0,6–1. */
  pulseAmp?: number;
  /** Ciągłe fale: odcisk oferty stale, pipeline w trakcie przebiegu. Pierwsza fala rusza
   *  od lewej przy zamontowaniu lub włączeniu. Przekazuj stałą referencję, nie literał. */
  loop?: StreamLoop;
  /** >0: wysokości etapów losowane z ziarna (odcisk oferty); 0: `ha`/`hb`. */
  seed?: number;
  depth?: number;
  width?: number;
  /** Czas przejścia fali w sekundach. */
  cross?: number;
  /** 1: jasna krawędź na czole wypełnienia. */
  edge?: number;
  ha?: [number, number, number, number];
  hb?: [number, number, number, number];
  /** Tylko laboratorium kształtu: nadpisuje fingerprint_shape.json. */
  shape?: FingerprintShape;
  className?: string;
}

export const Stream: React.FC<StreamProps> = ({
  progress,
  pulse,
  pulseAmp,
  loop,
  seed = 0,
  depth = 0.5,
  width = 20,
  cross = 4,
  edge = 1,
  ha = [0.18, 0.95, 0.95, 0.9],
  hb = [0.8, 0.55, 0.4, 0],
  shape,
  className,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<StreamRenderer | null>(null);
  const firstPulse = useRef(true);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    let r: StreamRenderer;
    try {
      r = new StreamRenderer(
        cv,
        { depth, width, cross, seed, edge, ha, hb, loop: loop ?? null },
        progress,
        shape ? buildFrag(shape) : FRAG,
      );
    } catch {
      return;
    }
    rendererRef.current = r;
    const ro = new ResizeObserver(() => r.resize());
    ro.observe(cv);
    return () => {
      ro.disconnect();
      r.dispose();
      rendererRef.current = null;
      // Kontekst oddajemy dopiero, gdy canvas naprawdę zniknął z dokumentu: przy podwójnym
      // montowaniu StrictMode ten sam canvas dostaje zaraz nowy renderer na tym samym kontekście.
      setTimeout(() => {
        if (!cv.isConnected) r.release();
      }, 0);
    };
    // Parametry kształtu są stałe dla danego miejsca; zmiana ziarna = nowy odcisk.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, shape]);

  useEffect(() => {
    rendererRef.current?.setTarget(progress);
  }, [progress]);

  useEffect(() => {
    rendererRef.current?.setLoop(loop ?? null);
  }, [loop]);

  useEffect(() => {
    if (pulse === undefined) return;
    if (firstPulse.current) {
      firstPulse.current = false;
      return;
    }
    rendererRef.current?.emit(pulseAmp);
  }, [pulse, pulseAmp]);

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
};

/** Stałe ziarno odcisku z linku oferty — ten sam kształt przy każdym otwarciu. */
export function seedFromLink(link: string): number {
  let h = 2166136261;
  for (let i = 0; i < link.length; i++) {
    h ^= link.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return 1 + (Math.abs(h) % 997);
}
