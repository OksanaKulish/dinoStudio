import { Component, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { CanvasComponent } from './graph/canvas/canvas/canvas';

@Component({
	imports: [RouterOutlet, CanvasComponent],
	selector: 'app-root',
	standalone: true,
	styleUrl: './app.scss',
	templateUrl: './app.html',
})
export class App {
	protected readonly title = signal('dinoStudio');
}
