'use client';

const CLARIFICATION_QUESTION =
  'To give you the most relevant answer, I need to know what category best describes your type of property. Please select an option below, or describe your property type.';

const PROPERTY_TYPE_CHOICES = [
  'Residential',
  'Commercial',
  'Manufacturing',
  'Agricultural',
  'Undeveloped',
  'Agricultural Forest',
  'Forest Land',
  'Farm Improvements (other)',
  'Not certain — general information',
];

interface DisambiguationData {
  result: 'disambiguate' | 'proceed';
  label: string;
  onSelect?: (choice: string) => void;
}

export function DisambiguationPane({ data }: { data: DisambiguationData }) {
  const fired = data.result === 'disambiguate';

  return (
    <div className="flex flex-col px-5 py-5">
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-2xl font-bold text-neutral-900">Disambiguation</h2>
        <p className="mt-1 text-sm text-neutral-500">{data.label}</p>
      </div>

      {fired ? (
        <>
          {/* Clarification question */}
          <div className="rounded-lg bg-neutral-50 border border-neutral-100 px-4 py-3 mb-4">
            <p className="text-sm text-neutral-700 leading-relaxed">{CLARIFICATION_QUESTION}</p>
          </div>

          {/* Choices */}
          <h3 className="text-sm font-bold text-neutral-900 mb-2">Offered Choices</h3>
          <div className="flex flex-wrap gap-2">
            {PROPERTY_TYPE_CHOICES.map((choice) => (
              <button
                key={choice}
                type="button"
                onClick={() => data.onSelect?.(choice)}
                className="cursor-pointer rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-700 transition-colors hover:bg-neutral-100 hover:border-neutral-300"
              >
                {choice}
              </button>
            ))}
          </div>

          {/* Short-circuit banner */}
          <div className="mt-5 rounded-md bg-red-50 px-3 py-2">
            <p className="text-xs font-medium text-red-700 uppercase tracking-wide">
              Agent loop short-circuited
            </p>
          </div>
        </>
      ) : (
        <div className="rounded-md bg-green-50 px-3 py-2">
          <p className="text-xs font-medium text-green-700 uppercase tracking-wide">
            Query specific enough — proceeding to agent loop
          </p>
        </div>
      )}
    </div>
  );
}
