"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

/**
 * A "living" sphere rendered with three.js: an icosahedron whose vertices are
 * displaced by a rolling noise field, breathing gently at idle and surging
 * with the live microphone amplitude (`level`, 0..1) while `listening` is true.
 */
export function VoiceOrb({
  listening,
  level,
  size = 128,
}: {
  listening: boolean;
  level: number;
  size?: number;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({ listening, level });
  stateRef.current = { listening, level };

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.z = 3.2;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(size, size);
    mount.appendChild(renderer.domElement);

    const geometry = new THREE.IcosahedronGeometry(1, 10);
    const basePositions = Float32Array.from(geometry.attributes.position.array);

    const material = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      emissive: 0x0ea5e9,
      emissiveIntensity: 0.5,
      roughness: 0.25,
      metalness: 0.35,
    });
    const sphere = new THREE.Mesh(geometry, material);
    scene.add(sphere);

    const wireGeometry = new THREE.IcosahedronGeometry(1.05, 1);
    const wireMaterial = new THREE.MeshBasicMaterial({
      color: 0x7dd3fc,
      wireframe: true,
      transparent: true,
      opacity: 0.22,
    });
    const wireSphere = new THREE.Mesh(wireGeometry, wireMaterial);
    scene.add(wireSphere);

    const keyLight = new THREE.PointLight(0x38bdf8, 3, 12);
    keyLight.position.set(2, 2, 2.5);
    scene.add(keyLight);
    const rimLight = new THREE.PointLight(0xf472b6, 1.8, 12);
    rimLight.position.set(-2, -1.2, 1.5);
    scene.add(rimLight);
    scene.add(new THREE.AmbientLight(0x223344, 1.1));

    let raf = 0;
    let t = 0;
    const noise = (x: number, y: number, z: number, tt: number) =>
      Math.sin(x * 2.3 + tt) * Math.cos(y * 2.5 + tt * 0.8) * Math.sin(z * 2.1 + tt * 1.3);
    const color = new THREE.Color();

    function animate() {
      t += 0.016;
      const { listening, level } = stateRef.current;
      const amp = listening ? 0.05 + level * 0.55 : 0.02 + 0.02 * Math.sin(t * 1.3);

      const pos = geometry.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        const ix = i * 3;
        const bx = basePositions[ix];
        const by = basePositions[ix + 1];
        const bz = basePositions[ix + 2];
        const n = noise(bx, by, bz, t * (listening ? 2.4 : 0.6));
        const scale = 1 + n * amp;
        pos.setXYZ(i, bx * scale, by * scale, bz * scale);
      }
      pos.needsUpdate = true;
      geometry.computeVertexNormals();

      sphere.rotation.y += listening ? 0.006 + level * 0.02 : 0.003;
      sphere.rotation.x += 0.0015;
      wireSphere.rotation.y -= 0.0025;
      wireSphere.rotation.x -= 0.001;

      const hue = listening ? 0.86 : 0.58; // idle: cyan/blue, listening: magenta/pink
      color.setHSL(hue, 0.85, 0.55 + (listening ? level * 0.18 : 0));
      material.color.lerp(color, 0.12);
      material.emissive.lerp(color, 0.12);
      material.emissiveIntensity = 0.45 + (listening ? level * 1.3 : 0.1);

      const s = 1 + (listening ? level * 0.14 : 0.02 * Math.sin(t * 1.3));
      sphere.scale.setScalar(s);
      wireSphere.scale.setScalar(s * 1.03);

      renderer.render(scene, camera);
      raf = requestAnimationFrame(animate);
    }
    animate();

    return () => {
      cancelAnimationFrame(raf);
      renderer.dispose();
      geometry.dispose();
      wireGeometry.dispose();
      material.dispose();
      wireMaterial.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [size]);

  return <div ref={mountRef} style={{ width: size, height: size }} className="pointer-events-none" />;
}
