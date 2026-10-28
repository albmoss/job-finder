type TrackEvent = 'cv_upload' | 'search_done' | 'signup' | 'offer_click';

declare global {
  interface Window {
    umami?: { track: (event: string, data?: Record<string, string>) => void };
  }
}

export function track(event: TrackEvent, data?: Record<string, string>) {
  window.umami?.track(event, data);
}
