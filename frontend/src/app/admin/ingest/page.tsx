'use client';

import { useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { http } from '@/lib/http';
import {
  Plus,
  Trash2,
  Upload,
  Loader2,
  CheckCircle2,
  XCircle,
  FileText,
  Globe,
} from 'lucide-react';

type DocCategory =
  | 'statutes'
  | 'admin_rules'
  | 'wpam'
  | 'faq_pages'
  | 'gov_publications'
  | 'news_pages'
  | 'complex_inquiry_pages'
  | 'constitution';

interface UrlEntry {
  id: string;
  url: string;
}

type JobStatus = 'idle' | 'validating' | 'ingesting' | 'complete' | 'error';

interface JobResult {
  status: 'success' | 'failed';
  url: string;
  doc_id?: string;
  size_bytes?: number;
  error?: string;
}

const CATEGORIES: { value: DocCategory; label: string; description: string }[] = [
  { value: 'statutes', label: 'Statutes', description: 'Wisconsin statute chapters (authority level 2)' },
  { value: 'admin_rules', label: 'Admin Rules', description: 'Tax administrative code (authority level 4)' },
  { value: 'wpam', label: 'WPAM', description: 'Property Assessment Manual (authority level 5)' },
  { value: 'faq_pages', label: 'FAQ Pages', description: 'Property tax FAQ pages (authority level 6)' },
  { value: 'gov_publications', label: 'Gov Publications', description: 'DOR publications and guides (authority level 7)' },
  { value: 'news_pages', label: 'News Pages', description: 'Assessor news and advisories (authority level 7)' },
  { value: 'complex_inquiry_pages', label: 'Complex Inquiry Pages', description: 'Advisory pages (authority level 7)' },
  { value: 'constitution', label: 'Constitution', description: 'Wisconsin constitution (authority level 1)' },
];

function generateId() {
  return Math.random().toString(36).slice(2, 10);
}

function IngestPage() {
  const [category, setCategory] = useState<DocCategory>('gov_publications');
  const [urls, setUrls] = useState<UrlEntry[]>([{ id: generateId(), url: '' }]);
  const [titleOverride, setTitleOverride] = useState('');
  const [jobStatus, setJobStatus] = useState<JobStatus>('idle');
  const [results, setResults] = useState<JobResult[]>([]);
  const [taskArn, setTaskArn] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState('');

  const addUrl = useCallback(() => {
    setUrls(prev => [...prev, { id: generateId(), url: '' }]);
  }, []);

  const removeUrl = useCallback((id: string) => {
    setUrls(prev => prev.length > 1 ? prev.filter(u => u.id !== id) : prev);
  }, []);

  const updateUrl = useCallback((id: string, value: string) => {
    setUrls(prev => prev.map(u => u.id === id ? { ...u, url: value } : u));
  }, []);

  const validUrls = urls.filter(u => u.url.trim().length > 0);

  const handleSubmit = async () => {
    if (validUrls.length === 0) return;

    setJobStatus('validating');
    setResults([]);
    setErrorMessage('');

    try {
      const response = await http.post('admin/ingest', {
        json: {
          urls: validUrls.map(u => u.url.trim()),
          category,
          title_override: titleOverride.trim() || undefined,
        },
        timeout: 120_000,
      }).json<{ statusCode?: number; body?: string; results?: JobResult[]; task_arn?: string; error?: string }>();

      const data = response.body ? JSON.parse(response.body) : response;

      if (data.error) {
        setJobStatus('error');
        setErrorMessage(data.error);
        return;
      }

      setResults(data.results || []);
      setTaskArn(data.task_arn || null);
      setJobStatus('complete');
    } catch (err: unknown) {
      setJobStatus('error');
      setErrorMessage(err instanceof Error ? err.message : 'Ingestion request failed');
    }
  };

  const reset = () => {
    setJobStatus('idle');
    setResults([]);
    setTaskArn(null);
    setErrorMessage('');
    setUrls([{ id: generateId(), url: '' }]);
    setTitleOverride('');
  };

  const isSubmitting = jobStatus === 'validating' || jobStatus === 'ingesting';

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-foreground">Ingest Documents</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Add new documents to the knowledge graph via URL
        </p>
      </div>

        {/* Form */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Document Details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {/* Category */}
            <div className="space-y-2">
              <Label>Category</Label>
              <select
                value={category}
                onChange={e => setCategory(e.target.value as DocCategory)}
                disabled={isSubmitting}
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              >
                {CATEGORIES.map(c => (
                  <option key={c.value} value={c.value}>
                    {c.label} — {c.description}
                  </option>
                ))}
              </select>
            </div>

            {/* URLs */}
            <div className="space-y-2">
              <Label>URLs</Label>
              <div className="space-y-2">
                {urls.map((entry, idx) => (
                  <div key={entry.id} className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <Globe className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                      <Input
                        placeholder={idx === 0 ? 'https://docs.legis.wisconsin.gov/...' : 'Another URL...'}
                        value={entry.url}
                        onChange={e => updateUrl(entry.id, e.target.value)}
                        disabled={isSubmitting}
                        className="pl-8"
                      />
                    </div>
                    {urls.length > 1 && (
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => removeUrl(entry.id)}
                        disabled={isSubmitting}
                        className="h-9 w-9 shrink-0 text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={addUrl}
                disabled={isSubmitting}
                className="mt-1"
              >
                <Plus className="mr-1 h-3 w-3" />
                Add URL
              </Button>
            </div>

            {/* Title Override */}
            <div className="space-y-2">
              <Label>
                Title Override <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                placeholder="Leave empty to auto-derive from document"
                value={titleOverride}
                onChange={e => setTitleOverride(e.target.value)}
                disabled={isSubmitting}
              />
            </div>

            <Separator />

            {/* Submit */}
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {validUrls.length === 0
                  ? 'Enter at least one URL to ingest'
                  : `${validUrls.length} URL${validUrls.length > 1 ? 's' : ''} ready`}
              </p>
              <Button
                onClick={handleSubmit}
                disabled={validUrls.length === 0 || isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {jobStatus === 'validating' ? 'Validating...' : 'Ingesting...'}
                  </>
                ) : (
                  <>
                    <Upload className="mr-2 h-4 w-4" />
                    Ingest
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Results */}
        {(jobStatus === 'complete' || jobStatus === 'error') && (
          <Card className="mt-4">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">
                  {jobStatus === 'error' ? 'Error' : 'Results'}
                </CardTitle>
                <Button variant="outline" size="sm" onClick={reset}>
                  Start New
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {jobStatus === 'error' && (
                <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{errorMessage}</span>
                </div>
              )}
              {results.length > 0 && (
                <div className="space-y-2">
                  {results.map((result, idx) => (
                    <div
                      key={idx}
                      className="flex items-center gap-3 rounded-md border p-3"
                    >
                      {result.status === 'success' ? (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600" />
                      ) : (
                        <XCircle className="h-4 w-4 shrink-0 text-destructive" />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{result.url}</p>
                        {result.doc_id && (
                          <p className="text-xs text-muted-foreground">
                            <FileText className="mr-1 inline h-3 w-3" />
                            {result.doc_id}
                            {result.size_bytes != null && (
                              <span className="ml-2">
                                ({(result.size_bytes / 1024).toFixed(0)} KB)
                              </span>
                            )}
                          </p>
                        )}
                        {result.error && (
                          <p className="text-xs text-destructive">{result.error}</p>
                        )}
                      </div>
                      <Badge variant={result.status === 'success' ? 'default' : 'destructive'}>
                        {result.status}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
              {taskArn && (
                <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 dark:border-blue-900 dark:bg-blue-950">
                  <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
                    Pipeline launched
                  </p>
                  <p className="mt-0.5 text-xs text-blue-700 dark:text-blue-300">
                    Running full pipeline (extract → embed → load). Monitor via CloudWatch logs.
                  </p>
                  <p className="mt-1 truncate font-mono text-[10px] text-blue-600 dark:text-blue-400">
                    {taskArn}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
  );
}

export default function AdminIngestPage() {
  return <IngestPage />;
}
