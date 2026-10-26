import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-10 sm:px-6 lg:px-8">
      <header>
        <p className="text-xs uppercase tracking-[0.18em] text-[color:var(--text-subtle)]">Module</p>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--text)]">{title}</h1>
        <p className="mt-1 text-sm text-[color:var(--text-subtle)]">{description}</p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Available configuration</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm text-[color:var(--text-subtle)]">
          <p>Use the setup pages to build your company structure first.</p>
          <div className="flex flex-wrap gap-2">
            <Link to="/setup/company" className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-[color:var(--surface-muted)]">
              Company setup <ArrowRight className="h-4 w-4" />
            </Link>
            <Link to="/setup/fleet" className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-[color:var(--surface-muted)]">
              Fleet setup <ArrowRight className="h-4 w-4" />
            </Link>
            <Link to="/setup/routes" className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-[color:var(--surface-muted)]">
              Routes setup <ArrowRight className="h-4 w-4" />
            </Link>
            <Link to="/people" className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-[color:var(--surface-muted)]">
              People <ArrowRight className="h-4 w-4" />
            </Link>
            <Link to="/counter" className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-[color:var(--surface-muted)]">
              Counter sales <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
