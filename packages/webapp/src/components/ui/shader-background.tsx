'use client';

import { ShaderGradientCanvas, ShaderGradient } from '@shadergradient/react';

export function ShaderBackground() {
  return (
    <div className="fixed inset-0 -z-10">
      <ShaderGradientCanvas
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
        }}
      >
        <ShaderGradient
          animate="on"
          brightness={1.5}
          cAzimuthAngle={180}
          cDistance={2.8}
          cPolarAngle={80}
          cameraZoom={13.65}
          color1="#0a0a0a"
          color2="#000000"
          color3="#4f4b46"
          grain="off"
          lightType="3d"
          positionX={0}
          positionY={0}
          positionZ={0}
          rotationX={50}
          rotationY={0}
          rotationZ={-60}
          shader="defaults"
          type="waterPlane"
          uAmplitude={0}
          uDensity={1.9}
          uFrequency={0}
          uSpeed={0.10}
          uStrength={0.5}
          uTime={8}
          wireframe={true}
        />
      </ShaderGradientCanvas>
    </div>
  );
}
