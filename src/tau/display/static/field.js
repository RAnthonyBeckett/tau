// Moving light field behind the TAU rings and frame.
// Three additive-blended layers: wave surface, drifting light points, and tilted orbit loops.

const surfaceVertexShader = `
attribute float aEdgeDist;
uniform float uTime;
uniform float uEnergy;
uniform float uSpeed;
uniform float uMaxHeight;
varying float vElevation;
varying float vEdgeDist;
varying vec2 vPos;

void main() {
  vPos = position.xy;
  vEdgeDist = aEdgeDist;

  float curH = mix(0.30, uMaxHeight, clamp((uEnergy - 0.12) / 0.88, 0.0, 1.0));
  float t = uTime * uSpeed;
  vec2 p = position.xy;

  vec2 d1 = vec2(0.9578, 0.2873);
  vec2 d2 = vec2(-0.6585, 0.7526);
  vec2 d3 = vec2(0.2873, -0.9578);
  vec2 d4 = vec2(-0.8944, -0.4472);

  float ph1 = dot(d1, p) * 1.7453 - t * 1.1;
  float ph2 = dot(d2, p) * 2.6180 - t * 1.6;
  float ph3 = dot(d3, p) * 3.9270 - t * 2.2;
  float ph4 = dot(d4, p) * 6.2832 - t * 2.9;

  float s1 = sin(ph1); float c1 = cos(ph1);
  float s2 = sin(ph2); float c2 = cos(ph2);
  float s3 = sin(ph3); float c3 = cos(ph3);
  float s4 = sin(ph4); float c4 = cos(ph4);

  float S = 0.38 * s1 + 0.28 * s2 + 0.20 * s3 + 0.14 * s4;
  vElevation = clamp(S * 0.5 + 0.5, 0.0, 1.0);

  float z = vElevation * curH;
  vec2 inPlane = -(d1 * (0.38 * c1) + d2 * (0.28 * c2) + d3 * (0.20 * c3) + d4 * (0.14 * c4)) * (0.08 * curH);

  gl_Position = projectionMatrix * modelViewMatrix * vec4(p + inPlane, z, 1.0);
}
`;

const surfaceFragmentShader = `
uniform float uEnergy;
uniform float uVoice;
uniform float uCoreRadius;
varying float vElevation;
varying float vEdgeDist;
varying vec2 vPos;

void main() {
  float r = length(vPos);
  float coreFade = smoothstep(uCoreRadius * 0.7, uCoreRadius * 1.5, r);
  float edgeFade = smoothstep(0.0, 0.45, vEdgeDist);
  float crestBright = mix(0.55, 1.0, vElevation);

  vec3 colRed = vec3(0.9961, 0.1176, 0.1176);
  vec3 colAmber = vec3(1.0, 0.6941, 0.2314);
  vec3 colTeal = vec3(0.1843, 0.8392, 0.7843);
  vec3 colTrough = mix(vec3(0.70, 0.18, 0.12), colTeal, clamp(uVoice * 1.2, 0.0, 1.0));

  vec3 surfCol = mix(colTrough, colAmber, vElevation);
  float edgeColFactor = smoothstep(0.05, 0.50, vEdgeDist);
  vec3 col = mix(colRed, surfCol, edgeColFactor);

  float gain = (0.55 + 0.45 * uEnergy) * crestBright * edgeFade * coreFade;
  gl_FragColor = vec4(col * gain, gain);
}
`;

const pointsVertexShader = `
attribute vec3 aRandom;
attribute float aEdgeDist;
uniform float uTime;
uniform float uEnergy;
uniform float uSpeed;
uniform float uMaxHeight;
varying float vTwinkle;
varying float vEdgeDist;
varying vec2 vPos;

void main() {
  vEdgeDist = aEdgeDist;
  vPos = position.xy;

  float curH = mix(0.30, uMaxHeight, clamp((uEnergy - 0.12) / 0.88, 0.0, 1.0));
  float t = uTime * uSpeed;

  vec2 drift = vec2(cos(aRandom.z), sin(aRandom.z)) * (sin(t * 0.35 + aRandom.y) * 0.18);
  vec2 p = position.xy + drift;

  vec2 d1 = vec2(0.9578, 0.2873);
  vec2 d2 = vec2(-0.6585, 0.7526);
  vec2 d3 = vec2(0.2873, -0.9578);
  vec2 d4 = vec2(-0.8944, -0.4472);

  float ph1 = dot(d1, p) * 1.7453 - t * 1.1;
  float ph2 = dot(d2, p) * 2.6180 - t * 1.6;
  float ph3 = dot(d3, p) * 3.9270 - t * 2.2;
  float ph4 = dot(d4, p) * 6.2832 - t * 2.9;

  float s1 = sin(ph1); float c1 = cos(ph1);
  float s2 = sin(ph2); float c2 = cos(ph2);
  float s3 = sin(ph3); float c3 = cos(ph3);
  float s4 = sin(ph4); float c4 = cos(ph4);

  float S = 0.38 * s1 + 0.28 * s2 + 0.20 * s3 + 0.14 * s4;
  float elev = clamp(S * 0.5 + 0.5, 0.0, 1.0);
  float z = elev * curH + 0.01;

  vec2 inPlane = -(d1 * (0.38 * c1) + d2 * (0.28 * c2) + d3 * (0.20 * c3) + d4 * (0.14 * c4)) * (0.08 * curH);

  vec4 mvPos = modelViewMatrix * vec4(p + inPlane, z, 1.0);
  gl_Position = projectionMatrix * mvPos;

  float energyScale = 0.8 + 0.6 * uEnergy;
  gl_PointSize = 56.0 * energyScale * (1.0 / -mvPos.z);

  vTwinkle = 0.45 + 0.55 * sin(t * aRandom.x + aRandom.y);
}
`;

const pointsFragmentShader = `
uniform float uEnergy;
uniform float uCoreRadius;
varying float vTwinkle;
varying float vEdgeDist;
varying vec2 vPos;

void main() {
  float d = length(gl_PointCoord - vec2(0.5)) * 2.0;
  if (d > 1.0) discard;

  float sprite = exp(-d * d * 3.5);
  float r = length(vPos);
  float coreFade = smoothstep(uCoreRadius * 0.7, uCoreRadius * 1.4, r);
  float edgeFade = smoothstep(0.0, 0.35, vEdgeDist);

  float gain = (0.65 + 0.35 * uEnergy) * vTwinkle * sprite * coreFade * edgeFade;
  vec3 col = mix(vec3(1.0, 0.65, 0.25), vec3(1.0, 0.95, 0.8), vTwinkle);

  gl_FragColor = vec4(col * gain, gain);
}
`;

const orbitVertexShader = `
attribute float aProgress;
varying float vProgress;

void main() {
  vProgress = aProgress;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const orbitFragmentShader = `
uniform float uTime;
uniform float uEnergy;
uniform float uSpeed;
uniform float uArcOffset;
uniform float uArcSpeed;
varying float vProgress;

void main() {
  float arcPos = fract(uArcOffset + uTime * uSpeed * uArcSpeed);
  float d = min(abs(vProgress - arcPos), 1.0 - abs(vProgress - arcPos));

  float arc = smoothstep(0.065, 0.0, d);

  float baseBright = mix(0.12, 0.26, uEnergy);
  float arcBright = mix(0.45, 0.80, uEnergy);
  float bright = baseBright + arc * arcBright;

  vec3 colBase = vec3(0.92, 0.42, 0.18);
  vec3 colArc = vec3(1.0, 0.88, 0.60);
  vec3 col = mix(colBase, colArc, arc);

  gl_FragColor = vec4(col * bright, bright);
}
`;

export function buildField(THREE, opts = {}) {
  const group = new THREE.Group();
  const rawCorners = opts.corners || [
    new THREE.Vector3(-3.6, 2.078, 0),
    new THREE.Vector3(3.6, 2.078, 0),
    new THREE.Vector3(0, -4.157, 0),
  ];

  const c0 = rawCorners[0], c1 = rawCorners[1], c2 = rawCorners[2];
  const cx = (c0.x + c1.x + c2.x) / 3;
  const cy = (c0.y + c1.y + c2.y) / 3;

  const scale = 1.10;
  const V = [
    new THREE.Vector3(cx + (c0.x - cx) * scale, cy + (c0.y - cy) * scale, 0),
    new THREE.Vector3(cx + (c1.x - cx) * scale, cy + (c1.y - cy) * scale, 0),
    new THREE.Vector3(cx + (c2.x - cx) * scale, cy + (c2.y - cy) * scale, 0),
  ];

  const edgeNormals = [];
  for (let s = 0; s < 3; s++) {
    const va = V[s];
    const vb = V[(s + 1) % 3];
    const dx = vb.x - va.x;
    const dy = vb.y - va.y;
    const len = Math.sqrt(dx * dx + dy * dy);
    let nx = dy / len, ny = -dx / len;
    if (nx * (cx - va.x) + ny * (cy - va.y) < 0) {
      nx = -nx;
      ny = -ny;
    }
    edgeNormals.push({ va, nx, ny });
  }

  function calcEdgeDist(px, py) {
    let minDist = 1e9;
    for (let s = 0; s < 3; s++) {
      const { va, nx, ny } = edgeNormals[s];
      const d = (px - va.x) * nx + (py - va.y) * ny;
      if (d < minDist) minDist = d;
    }
    return Math.max(0, minDist);
  }

  const isSmall = !!opts.small;
  const numLines = isSmall ? 35 : 70;
  const numPieces = 90;
  const coreRadius = opts.coreRadius || 0.46;

  // 1. WAVE SURFACE (three families of lines)
  const linePositions = [];
  const lineEdgeDists = [];

  for (let s = 0; s < 3; s++) {
    const va = V[s];
    const vb = V[(s + 1) % 3];
    const vc = V[(s + 2) % 3];

    for (let i = 0; i < numLines; i++) {
      const f = (i + 1) / (numLines + 1);
      const p0x = (1 - f) * vc.x + f * va.x;
      const p0y = (1 - f) * vc.y + f * va.y;
      const p1x = (1 - f) * vc.x + f * vb.x;
      const p1y = (1 - f) * vc.y + f * vb.y;

      for (let j = 0; j < numPieces; j++) {
        const t0 = j / numPieces;
        const t1 = (j + 1) / numPieces;
        const ax = (1 - t0) * p0x + t0 * p1x;
        const ay = (1 - t0) * p0y + t0 * p1y;
        const bx = (1 - t1) * p0x + t1 * p1x;
        const by = (1 - t1) * p0y + t1 * p1y;

        linePositions.push(ax, ay, 0, bx, by, 0);
        lineEdgeDists.push(calcEdgeDist(ax, ay), calcEdgeDist(bx, by));
      }
    }
  }

  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(linePositions, 3));
  lineGeo.setAttribute('aEdgeDist', new THREE.Float32BufferAttribute(lineEdgeDists, 1));

  const surfaceMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
      uEnergy: { value: 0.12 },
      uVoice: { value: 0 },
      uSpeed: { value: 1.0 },
      uMaxHeight: { value: 0.70 },
      uCoreRadius: { value: coreRadius },
    },
    vertexShader: surfaceVertexShader,
    fragmentShader: surfaceFragmentShader,
  });

  const lineMesh = new THREE.LineSegments(lineGeo, surfaceMat);
  group.add(lineMesh);

  // 2. POINTS OF LIGHT
  const numPoints = isSmall ? 333 : 1000;
  const pointPositions = [];
  const pointRandoms = [];
  const pointEdgeDists = [];

  let seed = 987654321;
  function rnd() {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  }

  for (let i = 0; i < numPoints; i++) {
    let r1 = rnd(), r2 = rnd();
    if (r1 + r2 > 1) {
      r1 = 1 - r1;
      r2 = 1 - r2;
    }
    const px = (1 - r1 - r2) * V[0].x + r1 * V[1].x + r2 * V[2].x;
    const py = (1 - r1 - r2) * V[0].y + r1 * V[1].y + r2 * V[2].y;

    pointPositions.push(px, py, 0);
    pointEdgeDists.push(calcEdgeDist(px, py));
    pointRandoms.push(
      1.5 + rnd() * 3.5,
      rnd() * Math.PI * 2,
      rnd() * Math.PI * 2
    );
  }

  const pointGeo = new THREE.BufferGeometry();
  pointGeo.setAttribute('position', new THREE.Float32BufferAttribute(pointPositions, 3));
  pointGeo.setAttribute('aRandom', new THREE.Float32BufferAttribute(pointRandoms, 3));
  pointGeo.setAttribute('aEdgeDist', new THREE.Float32BufferAttribute(pointEdgeDists, 1));

  const pointsMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
      uEnergy: { value: 0.12 },
      uSpeed: { value: 1.0 },
      uMaxHeight: { value: 0.70 },
      uCoreRadius: { value: coreRadius },
    },
    vertexShader: pointsVertexShader,
    fragmentShader: pointsFragmentShader,
  });

  const pointMesh = new THREE.Points(pointGeo, pointsMat);
  group.add(pointMesh);

  // 3. ORBIT LINES
  const numOrbits = 24;
  const orbitSegments = 96;
  const orbitData = [];
  const orbitMats = [];

  for (let k = 0; k < numOrbits; k++) {
    const norm = k / (numOrbits - 1);
    const baseR = 4.2 + norm * 2.3;
    const beta = 0.20 - norm * 0.08;

    const positions = [];
    const progress = [];

    for (let j = 0; j <= orbitSegments; j++) {
      const u = j / orbitSegments;
      const theta = u * Math.PI * 2;
      const r = baseR * (1.0 + beta * Math.sin(3 * theta));
      positions.push(r * Math.cos(theta), r * Math.sin(theta), 0);
      progress.push(u);
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('aProgress', new THREE.Float32BufferAttribute(progress, 1));

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: { value: 0 },
        uEnergy: { value: 0.12 },
        uSpeed: { value: 1.0 },
        uArcOffset: { value: (k * 0.173) % 1.0 },
        uArcSpeed: { value: (k % 2 === 0 ? 1 : -1) * (0.07 + 0.05 * (k % 4)) },
      },
      vertexShader: orbitVertexShader,
      fragmentShader: orbitFragmentShader,
    });

    const line = new THREE.Line(geo, mat);

    const tiltX = Math.sin(k * 1.8 + 0.3) * 0.15;
    const tiltY = Math.cos(k * 2.4 + 0.7) * 0.15;
    const spinSpeed = (k % 2 === 0 ? 1 : -1) * (0.02 + 0.04 * (1.0 - norm * 0.4));
    const baseAngle = (k * 0.38) % (Math.PI * 2);

    const orbitHolder = new THREE.Group();
    orbitHolder.rotation.x = tiltX;
    orbitHolder.rotation.y = tiltY;
    orbitHolder.add(line);
    group.add(orbitHolder);

    orbitData.push({ line, spinSpeed, baseAngle });
    orbitMats.push(mat);
  }

  let isReduced = false;
  function setReduced(bool) {
    isReduced = !!bool;
    const speed = isReduced ? 0.2 : 1.0;
    const maxHeight = isReduced ? 0.12 : 0.70;
    surfaceMat.uniforms.uSpeed.value = speed;
    surfaceMat.uniforms.uMaxHeight.value = maxHeight;
    pointsMat.uniforms.uSpeed.value = speed;
    pointsMat.uniforms.uMaxHeight.value = maxHeight;
    orbitMats.forEach((m) => { m.uniforms.uSpeed.value = speed; });
  }

  try {
    if (typeof window !== 'undefined' && window.matchMedia) {
      const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
      setReduced(mql.matches);
      if (mql.addEventListener) {
        mql.addEventListener('change', (e) => setReduced(e.matches));
      } else if (mql.addListener) {
        mql.addListener((e) => setReduced(e.matches));
      }
    }
  } catch (_) {}

  function update(t, energy, voice) {
    const e = Math.max(0, Math.min(1, energy));
    const v = Math.max(0, Math.min(1, voice));

    surfaceMat.uniforms.uTime.value = t;
    surfaceMat.uniforms.uEnergy.value = e;
    surfaceMat.uniforms.uVoice.value = v;

    pointsMat.uniforms.uTime.value = t;
    pointsMat.uniforms.uEnergy.value = e;

    const speedMult = isReduced ? 0.2 : 1.0;
    for (let k = 0; k < numOrbits; k++) {
      const d = orbitData[k];
      d.line.rotation.z = d.baseAngle + t * d.spinSpeed * speedMult;
      orbitMats[k].uniforms.uTime.value = t;
      orbitMats[k].uniforms.uEnergy.value = e;
    }
  }

  function stats() {
    return {
      lines: numLines * 3,
      points: numPoints,
      orbits: numOrbits,
    };
  }

  return {
    group,
    update,
    setReduced,
    stats,
  };
}
