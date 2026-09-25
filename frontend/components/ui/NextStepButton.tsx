import { ArrowRight } from "lucide-react";
import { Button } from "./Button";

export function NextStepButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <div className="flex justify-end border-t border-border pt-4">
      <Button variant="outline" onClick={onClick}>
        Next: {label} <ArrowRight className="h-4 w-4" />
      </Button>
    </div>
  );
}
