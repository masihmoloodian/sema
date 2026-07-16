import { ChatProvider } from './providers/types';

/** Return only reasoning levels accepted by the selected model. */
export function effortsForModel(provider: ChatProvider, model: string): readonly string[] {
  return provider.modelInfos.find((info) => info.id === model)?.efforts ?? provider.efforts ?? [];
}
