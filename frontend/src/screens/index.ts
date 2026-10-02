import type { ComponentType } from 'react';
import type { RouteName } from '../router';
import type { ScreenProps } from './types';
import { MatchedScreen } from './MatchedScreen';
import { SavedScreen } from './SavedScreen';
import { ApplicationsScreen } from './ApplicationsScreen';
import { MyCvScreen } from './MyCvScreen';
import { InstructionsScreen } from './InstructionsScreen';
import { TailorSetupScreen } from './TailorSetupScreen';
import { TailorReviewScreen } from './TailorReviewScreen';
import { ProgressScreen } from './ProgressScreen';
import { StartScreen } from './StartScreen';

export type { ScreenProps };

export const SCREENS: Record<RouteName, ComponentType<ScreenProps>> = {
  dopasowane: MatchedScreen,
  zapisane: SavedScreen,
  aplikacje: ApplicationsScreen,
  cv: MyCvScreen,
  'cv-instrukcje': InstructionsScreen,
  'cv-nowa': TailorSetupScreen,
  'cv-wersja': TailorReviewScreen,
  postep: ProgressScreen,
  start: StartScreen,
};
