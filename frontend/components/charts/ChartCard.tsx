import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";

export function ChartCard({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="pt-2">
        <div className="w-full h-64 sm:h-72">{children}</div>
      </CardContent>
    </Card>
  );
}
