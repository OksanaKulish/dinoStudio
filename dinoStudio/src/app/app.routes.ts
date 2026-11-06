import { Routes } from '@angular/router';
import { ScraperPageComponent } from './scraper/ui/scraper-page';
import { TrainingPageComponent } from './training/ui/training-page';
import { CanvasComponent } from './graph/canvas/canvas/canvas';

export const routes: Routes = [
	{ path: '', component: CanvasComponent },
	{ path: 'scraper', component: ScraperPageComponent },
	{ path: 'training', component: TrainingPageComponent },
	{ path: '**', redirectTo: '' },
];
