import React from 'react';
import ReactDOM from 'react-dom/client';
import '@fontsource/geist/400.css';
import '@fontsource/geist/500.css';
import '@fontsource/geist/600.css';
import '@fontsource/geist/700.css';
import '../styles/base.css';
import './public.css';
import { PublicApp } from './PublicApp';

const rootElement = document.getElementById('root');
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <PublicApp />
    </React.StrictMode>
  );
}
