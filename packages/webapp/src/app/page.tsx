import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

export default function Home() {
  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <Card className="w-[400px]">
        <CardHeader>
          <CardTitle>shadcn/ui Card Demo</CardTitle>
          <CardDescription>
            This is a demonstration of the shadcn/ui Card component with the
            custom CSS styles applied.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            The card component is working correctly with the new globals.css and
            scrollbar styles. You can see the custom theme variables and styling
            in action.
          </p>
        </CardContent>
        <CardFooter className="flex justify-between">
          <p className="text-xs text-muted-foreground">
            shadcn/ui initialized successfully
          </p>
        </CardFooter>
      </Card>
    </main>
  );
}
