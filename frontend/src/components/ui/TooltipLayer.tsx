import React, { useEffect, useRef } from 'react';

/* Jeden tooltip na całą aplikację. Element dostaje `data-tip` (nazwa) i opcjonalnie
   `data-kbd` (skrót). Pierwszy pokazuje się po 600 ms bezruchu; przy przesuwaniu między
   sąsiednimi ikonami kolejne pojawiają się od razu, dopóki kursor nie odpocznie 400 ms. */
export const TooltipLayer: React.FC = () => {
  const tipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const tip = tipRef.current;
    if (!tip) return;
    let showTimer = 0;
    let coolTimer = 0;
    let warm = false;

    const show = (el: HTMLElement) => {
      tip.textContent = el.dataset.tip ?? '';
      if (el.dataset.kbd) {
        const kbd = document.createElement('kbd');
        kbd.textContent = el.dataset.kbd;
        tip.appendChild(kbd);
      }
      const r = el.getBoundingClientRect();
      tip.style.left = '0px';
      tip.style.top = '0px';
      const w = tip.offsetWidth;
      const left = Math.min(window.innerWidth - w - 8, Math.max(8, r.left + r.width / 2 - w / 2));
      const above = r.top > 60;
      tip.style.left = `${Math.round(left)}px`;
      tip.style.top = `${Math.round(above ? r.top - 36 : r.bottom + 8)}px`;
      tip.style.transformOrigin = above ? '50% 100%' : '50% 0%';
      tip.classList.toggle('instant', warm);
      tip.classList.add('show');
      warm = true;
    };

    const onOver = (e: PointerEvent) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>('[data-tip]');
      if (!el || e.pointerType !== 'mouse') return;
      clearTimeout(showTimer);
      clearTimeout(coolTimer);
      if (warm) show(el);
      else showTimer = window.setTimeout(() => show(el), 600);
    };
    const onOut = (e: PointerEvent) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>('[data-tip]');
      if (!el || (e.relatedTarget instanceof Node && el.contains(e.relatedTarget))) return;
      clearTimeout(showTimer);
      tip.classList.remove('show');
      coolTimer = window.setTimeout(() => { warm = false; }, 400);
    };
    const hide = () => {
      clearTimeout(showTimer);
      tip.classList.remove('show');
    };

    document.addEventListener('pointerover', onOver);
    document.addEventListener('pointerout', onOut);
    document.addEventListener('pointerdown', hide);
    return () => {
      document.removeEventListener('pointerover', onOver);
      document.removeEventListener('pointerout', onOut);
      document.removeEventListener('pointerdown', hide);
      clearTimeout(showTimer);
      clearTimeout(coolTimer);
    };
  }, []);

  return <div ref={tipRef} className="tip" role="tooltip" />;
};
