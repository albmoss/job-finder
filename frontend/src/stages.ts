import { Archive, Handshake, MessagesSquare, Send, type LucideIcon } from 'lucide-react';
import type { Stage } from './types';

export const STAGES: { id: Stage; label: string; icon: LucideIcon; hint: string }[] = [
  { id: 'apply', label: 'Wysłane', icon: Send, hint: 'Tu trafiają oferty oznaczone jako wysłane.' },
  { id: 'interview', label: 'Rozmowy', icon: MessagesSquare, hint: 'Przeciągnij tu aplikację, gdy zaproszą Cię na rozmowę.' },
  { id: 'offer', label: 'Oferta pracy', icon: Handshake, hint: 'Przeciągnij tu aplikację, gdy dostaniesz ofertę.' },
  { id: 'archive', label: 'Zakończone', icon: Archive, hint: 'Przeciągnij tu rekrutacje, które się skończyły.' },
];
